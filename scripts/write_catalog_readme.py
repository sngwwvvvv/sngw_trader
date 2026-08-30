from sngw_trader.config import load_settings
from sngw_trader.data.catalog_writer import write_placeholder_note


if __name__ == "__main__":
    settings = load_settings()
    path = write_placeholder_note(settings.catalog_path)
    print(path)
