FROM {{ service_base_image.image }}

COPY ci/requirements.txt ci/requirements.txt
RUN hail-pip-install -r requirements.txt

COPY ci/setup.py ci/MANIFEST.in /ci/
RUN --mount=src=ci/ci,target=/ci/ci \
    hail-pip-install /ci

EXPOSE 5000
