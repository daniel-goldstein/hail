
def init_logging():
    import logging
    logging.basicConfig(format="%(asctime)-15s: %(severity)s: %(message)s",
                        level=logging.INFO)
