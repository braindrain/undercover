"""Reset the database to the business data in data/."""

from backend import db

if __name__ == "__main__":
    print(f"Reset {db.reset()}")
