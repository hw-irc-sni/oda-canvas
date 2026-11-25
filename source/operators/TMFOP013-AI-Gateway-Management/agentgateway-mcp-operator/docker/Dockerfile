# TMFOP013 AI Gateway Management: agentgateway MCP auto-registration. Build from this folder:
#   docker build -t tmforumodacanvas/agentgateway-mcp-operator:0.1.0-ai-knowledge .
FROM python:3.12.16-alpine3.23

WORKDIR /src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

COPY src .

ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
ARG CICD_BUILD_TIME
ENV CICD_BUILD_TIME=$CICD_BUILD_TIME
ARG GIT_COMMIT_SHA
ENV GIT_COMMIT_SHA=$GIT_COMMIT_SHA

# Non-root (the chart also sets runAsNonRoot). kopf looks up the user name, so the user must exist.
RUN addgroup -S -g 10001 operator && adduser -S -D -H -u 10001 -G operator operator
USER 10001:10001

CMD ["kopf", "run", "--all-namespaces", "--standalone", "--liveness=http://0.0.0.0:8080/healthz", "/src/agentgatewayMcpOperator.py"]
