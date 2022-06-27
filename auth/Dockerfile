FROM {{ hailgenetics_hailtop_slim_image.image }}

COPY auth/pinned-requirements.txt pinned-requirements.txt
RUN hail-pip-install -r pinned-requirements.txt

COPY auth/setup.py auth/MANIFEST.in /auth/
COPY auth/auth /auth/auth/
RUN hail-pip-install /auth

EXPOSE 5000
