FROM python:3.7-slim

COPY docker/hail-ubuntu/retry \
     docker/hail-ubuntu/hail-apt-get-install \
     docker/hail-ubuntu/hail-pip-install \
     /bin
COPY docker/hail-ubuntu/controller.sh /

RUN --mount=src=auth,target=auth,ro=true --mount=src=gear,target=gear,ro=true \
    file=$(mktemp) && \
    cat auth/requirements.txt gear/requirements.txt > $file && \
    hail-pip-install -r $file

COPY hail/python/setup-hailtop.py /hailtop/setup.py
COPY /hail_version /hailtop/hailtop/hail_version
COPY hail/python/MANIFEST.in /hailtop/MANIFEST.in
RUN --mount=src=hail/python/hailtop,target=/hailtop/hailtop/,ro=true \
    hail-pip-install /hailtop

COPY gear/setup.py /gear/setup.py
RUN --mount=src=gear/gear,target=/gear/gear,ro=true \
    hail-pip-install /gear

COPY web_common/setup.py web_common/MANIFEST.in /web_common/
RUN --mount=src=web_common/web_common,target=/web_common/web_common,ro=true \
    hail-pip-install /web_common

COPY auth/setup.py auth/MANIFEST.in /auth/
RUN --mount=src=auth/auth,target=/auth/auth,ro=true \
    hail-pip-install /auth

EXPOSE 5000
