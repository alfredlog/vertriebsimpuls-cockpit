import pandas as pd

# generate the product Data


def generate_products() -> pd.DataFrame:
    products = [
        ["P001", "Bundesanleihe 10J", "government_bond", 10, 1, False],
        ["P002", "Bundesobligation 5J", "government_bond", 5, 1, False],
        ["P003", "Pfandbrief 5J", "covered_bond", 5, 1, False],
        ["P004", "Pfandbrief 10J", "covered_bond", 10, 2, False],
        ["P005", "Unternehmensanleihe 5J", "corporate_bond", 5, 3, False],
        ["P006", "Schuldscheindarlehen 5J", "corporate_bond", 5, 3, False],
        ["P007", "Schuldscheindarlehen 7J", "corporate_bond", 7, 3, False],
        ["P008", "Zinsswap 5J", "rates_derivative", 5, 3, True],
        ["P009", "Zinsswap 10J", "rates_derivative", 10, 3, True],
        ["P010", "Zinscap 3J", "rates_derivative", 3, 2, True],
        ["P011", "Devisentermingeschäft 1J", "fx_derivative", 1, 2, True],
        ["P012", "Termingeld 3M", "money_market", 0.25, 1, False],
        ["P013", "Termingeld 12M", "money_market", 1, 1, False],
        ["P014", "Bonuszertifikat 3J", "structured_product", 3, 4, False],
        ["P015", "Renten-ETF", "fund", None, 3, False],
        ["P016", "Aktien-ETF", "fund", None, 4, False],
    ]

    columns = [
        "product_id",
        "name",
        "asset_class",
        "tenor_years",
        "risk_level",
        "is_hedging_product",
    ]

    return pd.DataFrame(products, columns=columns)
