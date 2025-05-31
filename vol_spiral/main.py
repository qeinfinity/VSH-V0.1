import logging

def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Vol-Spiral Hunter skeleton initialised – ready for directives.")

if __name__ == "__main__":
    main()
