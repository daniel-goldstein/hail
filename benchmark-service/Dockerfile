FROM {{ service_base_image.image }}

COPY benchmark-service/setup.py benchmark-service/MANIFEST.in  /benchmark-service/
RUN --mount=src=benchmark-service/benchmark,target=/benchmark-service/benchmark \
    hail-pip-install /benchmark-service && \
    hail-pip-install plotly pandas scipy numpy

EXPOSE 5000
