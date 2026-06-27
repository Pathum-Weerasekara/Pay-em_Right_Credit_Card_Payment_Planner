import os
import requests
from dotenv import load_dotenv

# Load env file
load_dotenv()

class YNABClient:
    def __init__(self, api_token=None):
        self.api_token = api_token or os.getenv("YNAB_API_TOKEN")
        self.base_url = "https://api.ynab.com/v1"

    @property
    def headers(self):
        if not self.api_token:
            raise ValueError("YNAB API Token is not configured. Please set it in the Settings page or .env file.")
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def verify_token(self):
        """Verifies if token is valid by hitting the /user endpoint"""
        url = f"{self.base_url}/user"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def get_budgets(self):
        """Fetches list of all budgets"""
        url = f"{self.base_url}/budgets"
        response = requests.get(url, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()["data"]["budgets"]

    def get_accounts(self, budget_id):
        """Fetches list of all accounts for a budget"""
        url = f"{self.base_url}/budgets/{budget_id}/accounts"
        response = requests.get(url, headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()["data"]["accounts"]

    def get_transactions_since(self, budget_id, since_date):
        """Fetches transactions since a specific date (YYYY-MM-DD)"""
        url = f"{self.base_url}/budgets/{budget_id}/transactions"
        params = {"since_date": since_date}
        response = requests.get(url, headers=self.headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json()["data"]["transactions"]

    def get_month_categories(self, budget_id, month_str):
        """
        Fetches categories and credit card category balances for a specific month (YYYY-MM-01)
        """
        # Ensure date format is YYYY-MM-01
        if len(month_str) == 7:
            month_date = f"{month_str}-01"
        else:
            month_date = month_str
        url = f"{self.base_url}/budgets/{budget_id}/months/{month_date}"
        response = requests.get(url, headers=self.headers, timeout=15)
        response.raise_for_status()
        return response.json()["data"]["month"]["categories"]
