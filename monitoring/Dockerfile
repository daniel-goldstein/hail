FROM {{ service_base_image.image }}

COPY monitoring/setup.py monitoring/MANIFEST.in  /monitoring/
RUN --mount=src=monitoring/montoring,target=/monitoring/monitoring \
    hail-pip-install /monitoring

EXPOSE 5000
