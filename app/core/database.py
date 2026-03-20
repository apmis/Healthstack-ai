from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import get_settings


@lru_cache
def get_mongo_client() -> MongoClient:
    settings = get_settings()
    return MongoClient(settings.mongodb)


def get_database() -> Database:
    settings = get_settings()
    return get_mongo_client()[settings.mongodb_db]


def ping_database() -> None:
    get_database().command("ping")


def close_mongo_client() -> None:
    if get_mongo_client.cache_info().currsize:
        get_mongo_client().close()
        get_mongo_client.cache_clear()
