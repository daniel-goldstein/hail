FROM {{ hailtop_image.image }}

COPY gear/linux-pinned-requirements.txt pinned-requirements.txt
RUN hail-pip-install -r pinned-requirements.txt

COPY gear/setup.py /gear/setup.py
RUN --mount=src=gear/gear,target=/gear/gear,ro=true \
    hail-pip-install /gear
