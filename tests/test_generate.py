import pytest

from vic.data.generate import generate_products


@pytest.fixture
def products():
    return generate_products()


def test_product_ids_are_unique(products):
    assert products["product_id"].is_unique, "Product IDs must be unique"


def test_product_id_format(products):
    assert products["product_id"].str.fullmatch(r"P\d{3}").all(), (
        "Product_id must have the format P001"
    )


def test_hedging_and_asset_match(products):
    is_derivative = products["asset_class"].isin({"rates_derivative", "fx_derivative"})
    assert (products["is_hedging_product"] == is_derivative).all(), (
        "is_hedging_product must be True if and only if the product is a derivative"
    )


def test_tenor_years_and_asset_class_match(products):
    is_fund = products["asset_class"] == "fund"
    assert (products["tenor_years"].isna() == is_fund).all(), (
        "the tenor_year must be NaN if and only if the product is fund"
    )


def test_risk_level_between_1_and_5(products):
    assert products["risk_level"].between(1, 5).all(), (
        "The risk_level must be in the interval from 1 to 5"
    )


def test_is_hedging_dtype(products):
    assert products["is_hedging_product"].dtype == "bool", "is_hedging must be boolean"
