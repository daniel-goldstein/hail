FROM {{ hailtop_image.image }}

COPY auth/linux-pinned-requirements.txt pinned-requirements.txt
RUN hail-pip-install -r pinned-requirements.txt

COPY gear/setup.py /gear/setup.py
RUN --mount=src=gear/gear,target=/gear/gear,ro=true \
    hail-pip-install /gear

COPY web_common/setup.py web_common/MANIFEST.in /web_common/
RUN --mount=src=web_common/web_common,target=/web_common/web_common \
    hail-pip-install /web_common

COPY auth/setup.py auth/MANIFEST.in /auth/
RUN --mount=src=auth/auth,target=/auth/auth \
    hail-pip-install /auth

EXPOSE 5000
