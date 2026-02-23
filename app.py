# app.py
import os, time, requests, json, re
from flask import Flask, session, render_template, request, redirect, url_for, session
import time
import re
from itsdangerous import URLSafeSerializer
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=False)


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    serializer = URLSafeSerializer(app.config['SECRET_KEY'])
    
    API_URL       = os.getenv('api_url_var')
    CLIENT_ID     = os.getenv('client_id_var')
    CLIENT_SECRET = os.getenv('client_secret_var')

    TOKEN_INFO = {"access_token": None, "expires_at": 0}
    GRAPHQL_URL = os.getenv('graph_api_url')

    def get_token():
        """Fetch a new token if expired or missing."""
        if not TOKEN_INFO["access_token"] or time.time() >= TOKEN_INFO["expires_at"]:
            print("Fetching new token...")
            response = requests.post(
                API_URL,
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "grant_type": "client_credentials"
                }
            )
            if response.status_code == 200:
                data = response.json()
                TOKEN_INFO["access_token"] = data["access_token"]
                TOKEN_INFO["expires_at"] = time.time() + data["expires_in"]
            else:
                return {"error": "Failed to fetch token", "details": response.text}, 500
        return TOKEN_INFO["access_token"]

    @app.route('/')
    def index():
        return render_template('index.html')

    ## Now add the contact verification process
    # Route to generate an obfuscated token from a folio key (for admin use)
    @app.route("/generate/<folio_key>")
    def generate_token(folio_key):
        token = serializer.dumps(folio_key)
        return f"Token for {folio_key}: <a href='/link/{token}'>/link/{token}</a>"

    # New route that uses a token (instead of a plain folio key) in the URL.
    @app.route("/link/<token>")
    def folio_page(token):
        try:
            # Decode the token to retrieve the original folio key.
            folio_key = serializer.loads(token)
        except Exception as e:
            return f"Invalid token: {str(e)}", 400

        token_api = get_token()
        if isinstance(token_api, dict) and token_api.get("error"):
            return f"Error fetching token: {token_api['error']}", 500

        headers = {"Authorization": f"Bearer {token_api}"}

        # Query for the entity using the folio key.
        query_entity = """
        query GetEntity($folioKey: String!) {
        folios(key: $folioKey) {
            nodes {
            entities {
                nodes {
                id
                name
                }
            }
            }
        }
        }
        """
        variables = {"folioKey": folio_key}
        res_entity = requests.post(
            GRAPHQL_URL,
            json={"query": query_entity, "variables": variables},
            headers=headers
        )

        if res_entity.status_code != 200:
            return f"Error fetching folio: {res_entity.text}", res_entity.status_code

        data_entity = res_entity.json()
        try:
            entity = data_entity["data"]["folios"]["nodes"][0]["entities"]["nodes"][0]
            entity_id = entity["id"]
        except (KeyError, IndexError):
            return f"Entity not found for folio key: {folio_key}", 404

        # Query for contacts using the entity's ID.
        query_contacts = """
        query GetContacts($entityIds: [ID!]!) {
        contacts(first: 10, entityIds: $entityIds) {
            edges {
            node {
                id
                name
                telephone
                email
            }
            }
        }
        }
        """
        variables = {"entityIds": [entity_id]}
        res_contacts = requests.post(
            GRAPHQL_URL,
            json={"query": query_contacts, "variables": variables},
            headers=headers
        )

        if res_contacts.status_code != 200:
            return f"Error fetching contacts: {res_contacts.text}", res_contacts.status_code

        data_contacts = res_contacts.json()
        try:
            contacts = [edge["node"] for edge in data_contacts["data"]["contacts"]["edges"]]
        except (KeyError, TypeError):
            contacts = []

        return render_template("folio.html", folio_key=folio_key, entity=entity, contacts=contacts)
    

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
