from common.config import get_target_locations
from common.open_meteo_utils import fetch_location_day, payload_to_dataframe


if __name__ == "__main__":
    location = get_target_locations()[0]
    payload = fetch_location_day(location, "2024-01-15")
    df = payload_to_dataframe(payload)
    print(df.head())
