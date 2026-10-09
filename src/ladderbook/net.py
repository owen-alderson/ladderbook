import ssl

import certifi

# python.org builds of Python ship without system CA certificates, so TLS uses certifi's bundle.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
