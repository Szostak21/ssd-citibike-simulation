from pipeline.download import download_station_information, download_tripdata, download_weather

if __name__ == "__main__":
    download_tripdata()
    download_station_information()
    download_weather()
