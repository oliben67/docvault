class DocVaultError(Exception):
    pass


class VaultNotFoundError(DocVaultError):
    pass


class DocumentNotFoundError(DocVaultError):
    pass


class TemplateNotFoundError(DocVaultError):
    pass


class TemplateValidationError(DocVaultError):
    pass


class SummarizationError(DocVaultError):
    pass


class AuthError(DocVaultError):
    pass
