FROM ensemblorg/ensembl-vep:release_95.3

USER root

RUN apt-get update && \
    apt-get install curl gpg jq && \
    curl 'https://keyserver.ubuntu.com/pks/lookup?search=0xF23C5A6CF475977595C89F51BA6932366A755776&hash=on&exact=on&options=mr&op=get' \
         | gpg --dearmor > /usr/share/keyrings/deadsnakes-ppa-archive-keyring.gpg && \
    echo 'deb [signed-by=/usr/share/keyrings/deadsnakes-ppa-archive-keyring.gpg] http://ppa.launchpad.net/deadsnakes/ppa/ubuntu xenial main' \
         >> /etc/apt/sources.list && \
    echo 'deb-src [signed-by=/usr/share/keyrings/deadsnakes-ppa-archive-keyring.gpg] http://ppa.launchpad.net/deadsnakes/ppa/ubuntu xenial main' \
         >> /etc/apt/sources.list && \
    apt-get install python3.8 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.8 1

ENV VEP_VERSION 95
ENV VEP_DIR /vep

ENV PERL5LIB "${PERL5LIB}:${VEP_DIR}"

RUN mkdir ${VEP_DIR}
WORKDIR ${VEP_DIR}

RUN cd ${VEP_DIR} && mkdir Plugins && \
    echo "#!/bin/bash" > /vep/vep && \
    echo "exec perl /opt/vep/src/ensembl-vep/vep \"\$@\"" >> /vep/vep && \
    chmod +x /vep/vep && \
    git clone -b postreleasefix/95 https://github.com/Ensembl/VEP_plugins.git Plugins/ && \
    git clone -b grch38 https://github.com/konradjk/loftee.git && \
    mv loftee/* Plugins/ && \
    rm Plugins/MaxEntScan.pm && \
    rm -r loftee

COPY vep.py /hail-vep/
