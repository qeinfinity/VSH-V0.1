# Configuration Loader
import configparser
import os
from dotenv import load_dotenv

def load_dot_env(dotenv_path=None):
    """Loads .env file."""
    load_dotenv(dotenv_path=dotenv_path)

def load_config(config_file_path='config.ini'):
    """Loads INI configuration file and expands environment variables."""
    config = configparser.ConfigParser()
    # Custom interpolation to handle environment variables like 
    # For a more robust solution, consider a custom interpolator or a different library
    # This is a simplified approach for demonstration
    raw_config = configparser.ConfigParser(interpolation=None) # Read raw values first
    if not raw_config.read(config_file_path):
        raise FileNotFoundError(f'Configuration file {config_file_path} not found.')

    for section in raw_config.sections():
        config.add_section(section)
        for key, value in raw_config.items(section):
            if value.startswith('${') and value.endswith('}'):
                env_var_name = value[2:-1]
                env_value = os.getenv(env_var_name)
                if env_value is None:
                    print(f'Warning: Environment variable {env_var_name} not set, but referenced in config.')
                    config.set(section, key, '') # Set to empty or raise error
                else:
                    config.set(section, key, env_value)
            else:
                config.set(section, key, value)
    return config
