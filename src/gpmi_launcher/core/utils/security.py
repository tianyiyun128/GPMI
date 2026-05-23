import base64

from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class Security:
    def __init__(self, public_key=None):
        self.public_key = None

        if public_key is not None:
            self.load_public_key(public_key)

    def verify(self, base64_signature, data, encoding='utf-8'):
        try:
            self.public_key.verify(self.decode(base64_signature), self.to_bytearray(data, encoding), ec.ECDSA(hashes.SHA256()))
            return True
        except Exception as e:
            return False

    def load_public_key(self, public_key):
        if Path(public_key).is_file():
            with open(Path(public_key), 'r') as f:
                public_key = f.read()
        der_bytes = self.decode(public_key)
        self.public_key = serialization.load_der_public_key(der_bytes)

    def serialize_public_key(self):
        return self.public_key.public_bytes(encoding=serialization.Encoding.DER,
                                            format=serialization.PublicFormat.SubjectPublicKeyInfo)

    def encode(self, data, encoding='utf-8'):
        return base64.b64encode(data).decode(encoding)

    def decode(self, data):
        return base64.b64decode(data)

    def to_bytearray(self, data, encoding):
        if isinstance(data, str):
            return bytearray(data, encoding)
        else:
            return bytearray(data)
