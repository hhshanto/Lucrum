"""CLI to register a new Walmart store: creates a `stores` row and stores its
encrypted credentials in `store_credentials`.

Usage:
    python add_store.py --name "Store A" --client-id ID --client-secret SECRET
"""

import argparse
import os

from dotenv import load_dotenv
from supabase import create_client

from secrets import encrypt

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Register a new Walmart store")
    parser.add_argument("--name", required=True, help="Display name for the store")
    parser.add_argument("--client-id", required=True, help="Walmart Marketplace client ID")
    parser.add_argument("--client-secret", required=True, help="Walmart Marketplace client secret")
    args = parser.parse_args()

    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    store = supabase.table("stores").insert({"name": args.name}).execute()
    store_id = store.data[0]["store_id"]

    supabase.table("store_credentials").insert({
        "store_id": store_id,
        "client_id": args.client_id,
        "encrypted_client_secret": encrypt(args.client_secret),
    }).execute()

    print(f"Created store {args.name!r} with store_id: {store_id}")


if __name__ == "__main__":
    main()
