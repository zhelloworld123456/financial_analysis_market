class FinancialAnalysisError(Exception):
    """可向用户展示的业务错误。"""


class InvalidStockQuery(FinancialAnalysisError):
    pass


class DataSourceError(FinancialAnalysisError):
    pass


class ModelError(FinancialAnalysisError):
    pass
