#!/bin/bash

# Check if enough arguments are provided
if [ $# -lt 20 ]; then
    echo "Usage: $0 <namespace> <repo-name> <container-maker-host> <container-maker-port> <container-maker-certs-secret-name> <cert-manager-cron-job-name> <browseterm-cloud-api-url> <postgres-host> <postgres-port> <postgres-user> <postgres-password> <postgres-db> <socket-ssh-host> <socket-ssh-wss-url> <ingress-host> <cookie-secure> <cookie-samesite> <payment-gateway-host> <payment-gateway-port> <payment-gateway-certs-secret-name>"
    exit 1
fi

YAML=./infra/deployment/deployment.yaml
NAMESPACE=$1
REPO_NAME=$2
CONTAINER_MAKER_HOST=$3
CONTAINER_MAKER_PORT=$4
CONTAINER_MAKER_CERTS_SECRET_NAME=$5
CERT_MANAGER_CRON_JOB_NAME=$6
# P07: Local no longer performs OAuth itself (see p07.md) - it just needs to know where Cloud is,
# not a redirect-base-URI or any Google/GitHub credential.
BROWSETERM_CLOUD_API_URL=$7
POSTGRES_HOST=$8
POSTGRES_PORT=$9
POSTGRES_USER=${10}
POSTGRES_PASSWORD=${11}
POSTGRES_DB=${12}
SOCKET_SSH_HOST=${13}
SOCKET_SSH_WSS_URL=${14}
INGRESS_HOST=${15}
COOKIE_SECURE=${16}
COOKIE_SAMESITE=${17}
PAYMENT_GATEWAY_HOST=${18}
PAYMENT_GATEWAY_PORT=${19}
PAYMENT_GATEWAY_CERTS_SECRET_NAME=${20}

export NAMESPACE=$NAMESPACE
export REPO_NAME=$REPO_NAME
export CONTAINER_MAKER_HOST=$CONTAINER_MAKER_HOST
export CONTAINER_MAKER_PORT=$CONTAINER_MAKER_PORT
export CONTAINER_MAKER_CERTS_SECRET_NAME=$CONTAINER_MAKER_CERTS_SECRET_NAME
export CERT_MANAGER_CRON_JOB_NAME=$CERT_MANAGER_CRON_JOB_NAME
export PAYMENT_GATEWAY_HOST=$PAYMENT_GATEWAY_HOST
export PAYMENT_GATEWAY_PORT=$PAYMENT_GATEWAY_PORT
export PAYMENT_GATEWAY_CERTS_SECRET_NAME=$PAYMENT_GATEWAY_CERTS_SECRET_NAME
export BROWSETERM_CLOUD_API_URL=$BROWSETERM_CLOUD_API_URL
export POSTGRES_HOST=$POSTGRES_HOST
export POSTGRES_PORT=$POSTGRES_PORT
export POSTGRES_USER=$POSTGRES_USER
export POSTGRES_PASSWORD=$POSTGRES_PASSWORD
export POSTGRES_DB=$POSTGRES_DB
export SOCKET_SSH_HOST=$SOCKET_SSH_HOST
export SOCKET_SSH_WSS_URL=$SOCKET_SSH_WSS_URL
export INGRESS_HOST=$INGRESS_HOST
export COOKIE_SECURE=$COOKIE_SECURE
export COOKIE_SAMESITE=$COOKIE_SAMESITE
envsubst < $YAML | kubectl apply -f -
