import os
from browseterm_db.common.config import DBConfig


# Container Maker Config
CONTAINER_MAKER_HOST: str = os.getenv("CONTAINER_MAKER_HOST", "container-maker-development-service")
CONTAINER_MAKER_PORT: int = int(os.getenv("CONTAINER_MAKER_PORT", "50052"))

# Kubernetes secret configuration for Container Maker certificates
CONTAINER_MAKER_CERTS_SECRET_NAME: str = os.getenv(
    "CONTAINER_MAKER_CERTS_SECRET_NAME",
    "container-maker-development-service-certs"
)

# Payment Gateway Config
PAYMENT_GATEWAY_HOST: str = os.getenv("PAYMENT_GATEWAY_HOST", "payment-gateway-development-service")
PAYMENT_GATEWAY_PORT: int = int(os.getenv("PAYMENT_GATEWAY_PORT", "50053"))

# Kubernetes secret configuration for Payment Gateway certificates
PAYMENT_GATEWAY_CERTS_SECRET_NAME: str = os.getenv(
    "PAYMENT_GATEWAY_CERTS_SECRET_NAME",
    "payment-gateway-development-service-certs"
)
# Kubernetes namespace for the application (used for cross-namespace service access)
NAMESPACE: str = os.getenv("NAMESPACE")

# Cert Manager Config
CERT_MANAGER_CRON_JOB_NAME: str = os.getenv("CERT_MANAGER_CRON_JOB_NAME")
CERT_MANAGER_CRON_JOB_NAMESPACE: str = os.getenv("CERT_MANAGER_CRON_JOB_NAMESPACE")


# Auth common config (P07 - see p07.md)
#
# Local no longer holds Google/GitHub OAuth client id/secret, provider URLs, or a Redis client at
# all - Cloud is the sole OAuth authority and the sole holder of those secrets
# (browseterm-server/src/common/config.py). Local's only auth-related config now is: where to
# send the browser to start OAuth (Cloud's public URL, src.cloud_client.config.
# BROWSETERM_CLOUD_API_URL) and how long the browser-side session cookie should live (a client
# hint only - Cloud's Redis TTL, extended on every authenticated call, is authoritative).
SESSION_COOKIE_MAX_AGE: int = int(os.getenv("SESSION_COOKIE_MAX_AGE", "86400"))

# Cookie Security Configuration
# Set secure=True in production (HTTPS), False in development (HTTP)
COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
# samesite options: "strict", "lax", or "none"
COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "lax")

# Socket SSH WebSocket Configuration
# Set via SOCKET_SSH_WSS_URL environment variable
# For Ingress: ws://browseterm.local/ws
# For production: wss://yourdomain.com/ws
SOCKET_SSH_WSS_URL: str = os.getenv("SOCKET_SSH_WSS_URL", "ws://localhost:8000")


# Postgres Configuration
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "blahbob")
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "blahbob")
DB_CONFIG: DBConfig = DBConfig(
    username=POSTGRES_USER,
    password=POSTGRES_PASSWORD,
    host=POSTGRES_HOST,
    port=POSTGRES_PORT,
    database=POSTGRES_DB
)


# Resource Request Ratios (request = limit * ratio)
# CPU: 10% of limit
RESOURCE_CPU_REQUEST_RATIO: float = float(os.getenv("RESOURCE_CPU_REQUEST_RATIO", "0.1"))
# Memory: 50% of limit
RESOURCE_MEMORY_REQUEST_RATIO: float = float(os.getenv("RESOURCE_MEMORY_REQUEST_RATIO", "0.5"))
# Ephemeral storage: 50% of limit
RESOURCE_EPHEMERAL_REQUEST_RATIO: float = float(os.getenv("RESOURCE_EPHEMERAL_REQUEST_RATIO", "0.5"))
