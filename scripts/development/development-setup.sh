#!/bin/bash

# Check if enough arguments are provided
if [ $# -lt 21 ]; then
    echo "Usage: $0 <namespace> <absolute-path-to-current-working-directory> <repo-name> <container-maker-host> <container-maker-port> <container-maker-certs-secret-name> <cert-manager-cron-job-name> <browseterm-cloud-api-url> <postgres-host> <postgres-port> <postgres-user> <postgres-password> <postgres-db> <socket-ssh-host> <socket-ssh-wss-url> <ingress-host> <payment-gateway-development-host> <payment-gateway-development-port> <payment-gateway-certs-secret-name> <cloud-ingress-host> <cloud-ingress-host-ip> [expected-kube-context]"
    exit 1
fi

YAML=./infra/development/development.yaml
NAMESPACE=$1
HOSTPATH=$2
REPO_NAME=$3
CONTAINER_MAKER_DEVELOPMENT_HOST=$4
CONTAINER_MAKER_DEVELOPMENT_PORT=$5
CONTAINER_MAKER_CERTS_SECRET_NAME=$6
CERT_MANAGER_CRON_JOB_NAME=$7
# P07: Local no longer performs OAuth itself (see p07.md) - it just needs to know where Cloud is,
# not a redirect-base-URI or any Google/GitHub credential.
BROWSETERM_CLOUD_API_URL=$8
POSTGRES_HOST=$9
POSTGRES_PORT=${10}
POSTGRES_USER=${11}
POSTGRES_PASSWORD=${12}
POSTGRES_DB=${13}
SOCKET_SSH_HOST=${14}
SOCKET_SSH_WSS_URL=${15}
INGRESS_HOST=${16}
PAYMENT_GATEWAY_DEVELOPMENT_HOST=${17}
PAYMENT_GATEWAY_DEVELOPMENT_PORT=${18}
PAYMENT_GATEWAY_CERTS_SECRET_NAME=${19}
# P13 - see the note in infra/deployment/deployment.yaml. See SETUP-LOCAL.md.
CLOUD_INGRESS_HOST=${20}
CLOUD_INGRESS_HOST_IP=${21}
# P23 (~/browseterm/p.md's "P23" section, plan section 22: "Every script checks kube context
# before applying"): this project runs two separate k3d clusters (Cloud/Local) reachable from the
# same host - applying against the wrong one is a real, previously-unguarded mistake class.
# Optional (empty = no check) so this doesn't break an existing call site not yet passing it.
EXPECTED_KUBE_CONTEXT=${22:-}
if [ -n "$EXPECTED_KUBE_CONTEXT" ]; then
    ACTUAL_KUBE_CONTEXT=$(kubectl config current-context)
    if [ "$ACTUAL_KUBE_CONTEXT" != "$EXPECTED_KUBE_CONTEXT" ]; then
        echo "ERROR: current kube context is '$ACTUAL_KUBE_CONTEXT', expected '$EXPECTED_KUBE_CONTEXT'. Aborting." >&2
        exit 1
    fi
fi

export NAMESPACE=$NAMESPACE
export HOSTPATH=$HOSTPATH
export REPO_NAME=$REPO_NAME
export CONTAINER_MAKER_DEVELOPMENT_HOST=$CONTAINER_MAKER_DEVELOPMENT_HOST
export CONTAINER_MAKER_DEVELOPMENT_PORT=$CONTAINER_MAKER_DEVELOPMENT_PORT
export CONTAINER_MAKER_CERTS_SECRET_NAME=$CONTAINER_MAKER_CERTS_SECRET_NAME
export CERT_MANAGER_CRON_JOB_NAME=$CERT_MANAGER_CRON_JOB_NAME
export PAYMENT_GATEWAY_DEVELOPMENT_HOST=$PAYMENT_GATEWAY_DEVELOPMENT_HOST
export PAYMENT_GATEWAY_DEVELOPMENT_PORT=$PAYMENT_GATEWAY_DEVELOPMENT_PORT
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
export CLOUD_INGRESS_HOST=$CLOUD_INGRESS_HOST
export CLOUD_INGRESS_HOST_IP=$CLOUD_INGRESS_HOST_IP
envsubst < $YAML | kubectl apply -f -
