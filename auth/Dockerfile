FROM {{ gear_image.image }}

COPY auth/linux-pinned-requirements.txt pinned-requirements.txt
RUN hail-pip-install -r pinned-requirements.txt

COPY auth/setup.py auth/MANIFEST.in /auth/
COPY auth/auth /auth/auth/
RUN hail-pip-install /auth

EXPOSE 5000
