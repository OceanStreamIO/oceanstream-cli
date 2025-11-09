def save_geoparquet_locally(dataframe, file_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.Table.from_pandas(dataframe)
    pq.write_table(table, file_path)


def load_geoparquet_locally(file_path):
    import pyarrow.parquet as pq
    table = pq.read_table(file_path)
    return table.to_pandas()
