FROM ensemblorg/ensembl-vep:release_95.3

ENV VEP_VERSION 95
ENV VEP_DIR /vep

ENV PERL5LIB "${PERL5LIB}:${VEP_DIR}"

USER root

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

USER vep

COPY vep.py /hail-vep/
