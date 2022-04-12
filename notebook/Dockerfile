FROM {{ service_base_image.image }}

COPY notebook/setup.py notebook/MANIFEST.in /notebook/
RUN --mount=src=notebook/notebook,target=/notebook/notebook \
    hail-pip-install /notebook && rm -rf /notebook

EXPOSE 5000

CMD ["python3", "-m", "notebook"]
