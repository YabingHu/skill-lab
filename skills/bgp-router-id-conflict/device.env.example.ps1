# Copy this file to device.env.ps1, then fill in local credentials.
# device.env.ps1 is ignored by Git. Never commit real passwords or keys.
$env:SSH_PORT = "22"
$env:SSH_USERNAME = ""
# Obtain your password from an approved local secret source.
# $env:SSH_PASSWORD = "<load from secret manager>"
$env:AUTH_MODEL = "password"
$env:KEY_FILENAME = ""
# fault.ps1 manages DEVICE_CONNECTOR_INTERACTIVE_OK; do not set it here.
