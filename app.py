# app.py
import os, time, requests
from flask import Flask, render_template
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

    # These must be the libraryFieldId values (not the instance field id).
    # Entity Operational Contact is a built-in field queried via entityContacts,
    # so its env var is no longer needed for customFieldResponses.
    FIELD_NOTIFICATION_CONTACT   = os.getenv("FIELD_NOTIFICATION_CONTACT")    # MDBGaWVsZC0xNTgxNQ
    FIELD_SIGNATORY_WITNESS      = os.getenv("FIELD_SIGNATORY_WITNESS")        # MDBGaWVsZC0xNTgxNw
    FIELD_AGREEMENT_SIGNATORIES  = os.getenv("FIELD_AGREEMENT_SIGNATORIES")    # MDBGaWVsZC0xNTg0Ng

    CUSTOM_CONTACT_FIELDS = [
        ("notification_contact",  FIELD_NOTIFICATION_CONTACT),
        ("signatory_witness",     FIELD_SIGNATORY_WITNESS),
        ("agreement_signatories", FIELD_AGREEMENT_SIGNATORIES),
    ]
    # Drop any that aren't configured
    CUSTOM_CONTACT_FIELDS = [(k, v) for k, v in CUSTOM_CONTACT_FIELDS if v]

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

    def gql(headers, query, variables=None):
        """Execute a GraphQL query, returning (data, error_string)."""
        res = requests.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers
        )
        if res.status_code != 200:
            return None, f"HTTP {res.status_code}: {res.text}"
        body = res.json()
        if body.get("errors"):
            return None, "GraphQL error(s): " + "; ".join(
                e.get("message", str(e)) for e in body["errors"]
            )
        return body.get("data"), None

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route("/generate/<folio_key>")
    def generate_token(folio_key):
        token = serializer.dumps(folio_key)
        return f"Token for {folio_key}: <a href='/link/{token}'>/link/{token}</a>"

    @app.route("/link/<token>")
    def folio_page(token):
        # ----------------------------
        # 1) Decode + normalise folio key
        # ----------------------------
        try:
            folio_key = serializer.loads(token)
            if isinstance(folio_key, dict):
                folio_key = folio_key.get("folio_key") or folio_key.get("folioKey") or folio_key.get("key")
            if isinstance(folio_key, bytes):
                folio_key = folio_key.decode("utf-8")
            folio_key = (folio_key or "").strip()
            if not isinstance(folio_key, str) or not folio_key:
                return f"Invalid folio key decoded from token: {repr(folio_key)}", 400
        except Exception as e:
            return f"Invalid token: {str(e)}", 400

        # ----------------------------
        # 2) Auth
        # ----------------------------
        token_api = get_token()
        if isinstance(token_api, dict) and token_api.get("error"):
            return f"Error fetching token: {token_api['error']}", 500
        headers = {"Authorization": f"Bearer {token_api}"}

        # ----------------------------
        # 3) Base folio query: key, title, main entity, built-in entityContacts
        #    Entity Operational Contact is a built-in field on the folio,
        #    not a customFieldResponse, so it's fetched here directly.
        # ----------------------------
        query_base = """
        query GetFolioBase($folioKey: String!) {
          folios(key: $folioKey) {
            nodes {
              key
              title
              entities(first: 1) {
                nodes { id name }
              }
              entityContacts(first: 2) {
                nodes { id name email telephone }
              }
            }
          }
        }
        """

        data_base, err = gql(headers, query_base, {"folioKey": folio_key})
        if err:
            return err, 500

        try:
            folio_node = data_base["folios"]["nodes"][0]
        except (KeyError, IndexError, TypeError):
            return f"Folio not found for folio key: {folio_key}", 404

        try:
            entity = folio_node["entities"]["nodes"][0]
        except (KeyError, IndexError, TypeError):
            return f"Entity not found for folio key: {folio_key}", 404

        folio_contact_fields = {
            "entity_operational_contact": folio_node.get("entityContacts", {}).get("nodes", []),
            "notification_contact":       [],
            "signatory_witness":          [],
            "agreement_signatories":      [],
        }

        # ----------------------------
        # 4) One request per custom contact field (avoids complexity limits)
        # ----------------------------
        query_single_field = """
        query GetFolioContactField($folioKey: String!, $fieldId: ID!) {
          folios(key: $folioKey) {
            nodes {
              customFieldResponses(libraryFieldIds: [$fieldId]) {
                field { libraryFieldId }
                ... on ContactLookupResponse {
                  contacts(first: 2) {
                    nodes { id name email telephone }
                  }
                }
              }
            }
          }
        }
        """

        for field_key, lib_id in CUSTOM_CONTACT_FIELDS:
            data_field, err = gql(headers, query_single_field, {
                "folioKey": folio_key,
                "fieldId": lib_id
            })
            if err:
                print(f"Warning: could not fetch field {field_key} ({lib_id}): {err}")
                continue

            try:
                responses = data_field["folios"]["nodes"][0]["customFieldResponses"]
                for resp in responses:
                    contacts = (resp.get("contacts") or {}).get("nodes") or []
                    if contacts:
                        folio_contact_fields[field_key] = contacts
                        break
            except (KeyError, IndexError, TypeError):
                pass

        # ----------------------------
        # 5) Render
        # ----------------------------
        return render_template(
            "folio.html",
            folio_key=folio_key,
            entity=entity,
            folio_contact_fields=folio_contact_fields,
        )

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)