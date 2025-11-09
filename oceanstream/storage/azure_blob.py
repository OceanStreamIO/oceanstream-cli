import os
from azure.storage.blob import BlobServiceClient


def upload_to_azure_blob(file_path: str, container_name: str, blob_name: str) -> None:
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        raise ValueError("Azure Storage connection string is not set in environment variables.")
    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    with open(file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)


def list_blobs(container_name: str) -> None:
    connect_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connect_str:
        raise ValueError("Azure Storage connection string is not set in environment variables.")
    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    container_client = blob_service_client.get_container_client(container_name)
    print(f"Listing blobs in container: {container_name}")
    for blob in container_client.list_blobs():
        print(blob.name)
