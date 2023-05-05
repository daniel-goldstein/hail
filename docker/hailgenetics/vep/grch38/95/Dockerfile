FROM ensemblorg/ensembl-vep:release_95.1

USER root

RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.5 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.5 1

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
