FROM {{ service_base_image.image }}

COPY auth/requirements.txt auth/requirements.txt
RUN hail-pip-install -r auth/requirements.txt

COPY auth/setup.py auth/MANIFEST.in /auth/
RUN --mount=src=auth/auth,target=/auth/auth \
    hail-pip-install /auth

EXPOSE 5000
