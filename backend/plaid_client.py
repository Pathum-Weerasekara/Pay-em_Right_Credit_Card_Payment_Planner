import os
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from datetime import date

class PlaidClientWrapper:
    def __init__(self):
        # Determine environment
        plaid_env = os.getenv("PLAID_ENV", "sandbox").lower()
        if plaid_env == "sandbox":
            host = plaid.Environment.Sandbox
        elif plaid_env == "development":
            host = plaid.Environment.Development
        elif plaid_env == "production":
            host = plaid.Environment.Production
        else:
            host = plaid.Environment.Sandbox
            
        configuration = plaid.Configuration(
            host=host,
            api_key={
                'clientId': os.getenv("PLAID_CLIENT_ID"),
                'secret': os.getenv("PLAID_SECRET"),
            }
        )
        api_client = plaid.ApiClient(configuration)
        self.client = plaid_api.PlaidApi(api_client)

    def create_link_token(self, client_user_id: str = "user-id"):
        # Setup link token request
        request = LinkTokenCreateRequest(
            products=[Products('transactions')],
            client_name="Pay'em Right CC Planner",
            country_codes=[CountryCode('US')],
            language='en',
            user=LinkTokenCreateRequestUser(client_user_id=client_user_id)
        )
        response = self.client.link_token_create(request)
        return response.to_dict()

    def exchange_public_token(self, public_token: str):
        request = ItemPublicTokenExchangeRequest(
            public_token=public_token
        )
        response = self.client.item_public_token_exchange(request)
        return response.to_dict()

    def get_accounts_and_balances(self, access_token: str):
        request = AccountsBalanceGetRequest(
            access_token=access_token
        )
        response = self.client.accounts_balance_get(request)
        return response.to_dict()

    def get_transactions(self, access_token: str, start_date: date, end_date: date):
        request = TransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
            options=TransactionsGetRequestOptions()
        )
        response = self.client.transactions_get(request)
        return response.to_dict()
