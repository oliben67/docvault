from __future__ import annotations


class DocVaultError(Exception):
    pass


class VaultNotFoundError(DocVaultError):
    pass


class DocumentNotFoundError(DocVaultError):
    pass


class StoreNotFoundError(DocVaultError):
    pass


class StoreValidationError(DocVaultError):
    pass


class SummarizationError(DocVaultError):
    pass


class AuthError(DocVaultError):
    pass
