import os
import sys
import oyaml as yaml
import json
import logging
import logging.config
import requests

from pyapic import APIConnect

# Hide SSL verify warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'standard': { 'format': '[%(levelname)s] - %(message)s' },
        'github':   { 'format': '::%(levelname)s::%(message)s' },
    },
    'handlers': {
        'default': { 'level': 'DEBUG', 'formatter': 'standard', 'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout' },
        'github':  { 'level': 'DEBUG', 'formatter': 'github', 'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout' },
    },
    'loggers': {
        '':       { 'handlers': ['default'], 'level': 'DEBUG', 'propagate': False },
        'github': {  'handlers': ['github'], 'level': 'DEBUG', 'propagate': False }
    }
})

# Github logging format
logging.addLevelName(logging.DEBUG, 'debug')
logging.addLevelName(logging.INFO, 'debug')
logging.addLevelName(logging.WARNING, 'warning')
logging.addLevelName(logging.ERROR, 'error')

logger = logging.getLogger()
github_logger = logging.getLogger('github')


def load_yaml(filename, encoding='utf-8'):
    with open(filename, 'r', encoding=encoding) as file:
        return yaml.safe_load(file)


def prepare_product(product_file, clean_api_reference=False):
    """Creates the payload for a product publish request"""

    # Load the product
    product = load_yaml(product_file)

    # This will hold the files to upload to publish the product
    files = []

    # Loop through the product APIs to get the files that will be uploaded
    for name, api_definition in product['apis'].items():
        if "name" in api_definition.keys():
            '''
            If the API is defined by name and version, we need to search through the filesystem
            for a yaml fil that contains the API and version specified
            '''
            raise Exception("Formato de producto no soportado 'name:'", None)
        if "$ref" in api_definition.keys():
            logger.debug(f"API $ref {api_definition['$ref']}")
            '''
            If the API is defined as a external reference, we need to solve it and transform the
            definition from:
                $ref: resourceconfigurationrest.yaml
            to:
                name: 'resourceconfigurationrest:1.1'
            '''
            product_path = os.path.dirname(product_file)
            logger.debug(f"Product path: {product_path}")

            api_path = os.path.dirname(api_definition['$ref'])
            logger.debug(f"API basepath: {api_path}")

            # Clean the API reference name
            if clean_api_reference:
                clean_name = api_definition['$ref'].split('_')[0] + ".yaml"
                logger.debug(f"Cleaned {api_definition['$ref']} to {clean_name}")
            else:
                clean_name = api_definition['$ref']

            logger.debug(f"Clean name: {clean_name}")

            # Load the API
            api_filename = os.path.join(product_path, clean_name)
            logger.debug(f"API filename: {api_filename}")
            api = load_yaml(api_filename, encoding='utf-8')

            # Transform the reference from $ref to name
            api_definition['name'] = f"{api['info']['x-ibm-name']}:{api['info']['version']}"
            logger.debug(f"Translated {api_definition['$ref']} to {api_definition['name']}")
            del api_definition['$ref']

            # Add the API file to the publish order
            files.append(
                ('openapi', ('openapi', open(api_filename, 'rb'), 'application/yaml'))
            )
            logger.info(f"Added API {api_filename} to the publish order")

            # If the API has a WSDL definition, add it to the publish order
            if api['x-ibm-configuration']['type'] == "wsdl" and 'wsdl-definition' in api['x-ibm-configuration']:
                wsdl_filename = os.path.join(product_path, api['x-ibm-configuration']['wsdl-definition']['wsdl'])
                files.append(
                    ('wsdl', ('wsdl', open(wsdl_filename, 'rb'), 'application/zip'))
                )
                logger.info(f"Added WSDL {wsdl_filename} to the publish order")

    # Dump the product to a temporal file that doesn't have $ref references
    temp_path = os.path.dirname(os.path.realpath(__file__))
    temp_product = os.path.join(temp_path, 'to_deploy.yaml')
    with open(temp_product, 'w', encoding='utf-8') as stream:
        yaml.dump(product, stream)

    # Add the product file to the publish order
    files.append(
        ('product', ('product', open(temp_product, 'rb'), 'application/yaml')),
    )
    logger.info(f"Added product {temp_product} to the publish order")

    return files

def main():

    # Input parameters as environment variables
    product_file = os.getenv("INPUT_PRODUCTFILE")
    manager_host = os.getenv("INPUT_MANAGERHOST")
    manager_usrname = os.getenv("INPUT_MANAGERUSERNAME")
    manager_password = os.getenv("INPUT_MANAGERPASSWORD")
    manager_realm = os.getenv("INPUT_MANAGERREALM")
    catalog = os.getenv("INPUT_CATALOG")
    organization = os.getenv("INPUT_ORGANIZATION")
    space = os.getenv("INPUT_SPACE", None)
    needs_subscription = os.getenv("INPUT_SUBSCRIBE") in ['true', '1']
    application = os.getenv("INPUT_APPLICATION")
    plan = os.getenv("INPUT_PLAN")
    consumer_organization = os.getenv("INPUT_CONSUMERORGANIZATION")

    apic = APIConnect(manager=manager_host, debug=False)
    apic.verify_ssl = False

    # Login
    apic.login(manager_usrname, manager_password, manager_realm)
    github_logger.info(f"Logged in to API Conned")

    # Prepare the product
    product = load_yaml(product_file)
    product_payload = prepare_product(product_file)

    # Publish the product
    github_logger.info("Publishing the product...")
    published_product = apic.product_publish(organization, catalog, None, product_payload, space)
    github_logger.info("Published the product")

    # Get product status
    product_version = product['info']['version']
    product_name = product['info']['name']
    product_title = product['info']['title']
    github_logger.info("Checking the product...")
    published_product = apic.product_get(organization, catalog, product_name, product_version)
    # print(json.dumps(published_product, indent=2))
    github_logger.info("Checked the product")

    # Return the status of the product
    product_state = published_product.get('state', None)
    print(f"::set-output name=result::{product_state}")

    # Subscribe the application to the product
    if product_state != "published":
        raise Exception("Product not published correctly")

    if needs_subscription:
        # product_url = published_product.get('url')
        product_id = published_product.get('id')
        product_url = f"https://apict-api-manager-ui.internal.vodafone.com/api/catalogs/{organization}/{catalog}/products/{product_id}"

        github_logger.info("Creating the subscription...")
        subscription = apic.subscription_create(product_url, organization, catalog, application, plan, consumer_organization)
        # print(json.dumps(subscription, indent=2))
        if subscription.get('state') != "enabled":
            raise Exception("Subscription not enabled")
        github_logger.info("Subscription created and enabled.")

    # Operation resume
    resume = f"Published the {product_title} product to catalog {catalog}"
    if space:
        resume += f" space {space}"
    if needs_subscription:
        resume += f" and subscribed {application} app to {plan} plan"

    print(f"::set-output name=resume::{resume}")

if __name__ == "__main__":
    try:
        main()
        exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        github_logger.error(str(e))
        exit(99)
