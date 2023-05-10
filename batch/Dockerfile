FROM {{ hail_ubuntu_image.image }}

RUN hail-apt-get-install unixodbc dpkg-dev lsb-release wget

RUN curl --remote-name https://dev.mysql.com/get/mysql-apt-config_0.8.25-1_all.deb && \
    dpkg -i mysql-apt-config_0.8.25-1_all.deb && \
    hail-apt-get-install mysql-connector-odbc

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.8 1

COPY hail/python/hailtop/pinned-requirements.txt hailtop-requirements.txt
COPY gear/pinned-requirements.txt gear-requirements.txt
COPY web_common/pinned-requirements.txt web_common-requirements.txt
COPY batch/pinned-requirements.txt batch-requirements.txt
RUN hail-pip-install \
      -r hailtop-requirements.txt \
      -r gear-requirements.txt \
      -r web_common-requirements.txt \
      -r batch-requirements.txt

COPY hail/python/setup-hailtop.py /hailtop/setup.py
COPY hail/python/MANIFEST.in /hailtop/MANIFEST.in
COPY hail/python/hailtop /hailtop/hailtop/

COPY gear/setup.py /gear/setup.py
COPY gear/gear /gear/gear/

COPY web_common/setup.py web_common/MANIFEST.in /web_common/
COPY web_common/web_common /web_common/web_common/

COPY batch/setup.py /batch/MANIFEST.in /batch/
COPY batch/batch /batch/batch/

RUN hail-pip-install /hailtop /gear /web_common /batch

EXPOSE 5000
