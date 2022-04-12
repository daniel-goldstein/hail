FROM {{ service_base_image.image }}

COPY memory/requirements.txt memory/requirements.txt
RUN hail-pip-install -r memory/requirements.txt

COPY memory/setup.py /memory/
RUN --mount=src=memory/memory,target=/memory/memory \
    hail-pip-install /memory

EXPOSE 5000
