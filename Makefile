build:
	@docker build -t jmorenoamor/api-connect-publish-action:latest .

test:
	@docker run --rm jmorenoamor/api-connect-publish-action
