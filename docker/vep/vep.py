#!/usr/bin/env python3

import gzip
import io
import json
import os
import re
import shlex
import subprocess as sp
import sys
import time
from typing import List, Optional


CONSEQUENCE_REGEX = re.compile(r'CSQ=[^;^\t]+')
CONSEQUENCE_HEADER_REGEX = re.compile(r'ID=CSQ[^>]+Description="([^"]+)')


def grouped_iterator(n, it):
    group = []
    while True:
        if len(group) == n:
            yield group
            group = []

        try:
            elt = next(it)
        except StopIteration:
            break

        group.append(elt)

    if group:
        yield group


VCF_HEADER = '''##fileformat=VCFv4.1
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
'''


class Variant:
    @staticmethod
    def from_vcf_line(l):
        fields = l.split('\t')
        contig = fields[0]
        pos = fields[1]
        ref = fields[3]
        alts = fields[4].split(',')
        return Variant(contig, pos, ref, alts)

    @staticmethod
    def from_string(s):
        fields = s.split(':')
        contig = fields[0]
        pos = fields[1]
        ref = fields[2]
        alts = fields[3].split(',')
        return Variant(contig, pos, ref, alts)

    def __init__(self, contig, pos, ref, alts):
        self.contig = contig
        self.position = pos
        self.ref = ref
        self.alts = alts

    def to_vcf_line(self):
        v = self.strip_star_allele()
        alts_str = ",".join(v.alts)
        return str(v.contig) + '\t' + str(v.position) + '\t.\t' + str(v.ref) + '\t' + alts_str + '\t.\t.\n'

    def strip_star_allele(self):
        return Variant(self.contig, self.position, self.ref, [a for a in self.alts if a != '*'])

    def __str__(self):
        return str(self.contig) + ':' + str(self.position) + ':' + str(self.ref) + ':' + ",".join(self.alts)


def consume_header(f: io.TextIOWrapper) -> str:
    header = ''
    pos = f.tell()
    line = f.readline()
    while line.startswith('#'):
        header += line
        pos = f.tell()
        line = f.readline()
    f.seek(pos)
    return header


def get_csq_header(vep_cmd: List[str]):
    with sp.Popen(vep_cmd, env=os.environ, stdin=sp.PIPE, stdout=sp.PIPE) as proc:
        v = Variant(1, 13372, 'G', ['C'])
        data = VCF_HEADER + '\n' + v.to_vcf_line()

        stdout_bytes, stderr_bytes = proc.communicate(data.encode())
        if stderr_bytes:
            print(stderr_bytes)

        stdout = stdout_bytes.decode()
        for line in stdout.split('\n'):
            line = line.rstrip()
            for match in CONSEQUENCE_HEADER_REGEX.finditer(line):
                return match.group(1)
        print('WARNING: could not get VEP CSQ header')
        return None


def run_vep(vep_cmd, input_file, block_size, consequence, tolerate_parse_error, part_id, env):
    results = []

    with open(input_file, 'r') as inp:
        header = consume_header(inp)
        for block_id, block in enumerate(grouped_iterator(block_size, inp)):
            n_processed = len(block)
            start_time = time.monotonic()

            variants = [Variant.from_vcf_line(l.rstrip()) for l in block]
            non_star_to_orig_variants = {str(v.strip_star_allele()): v for v in variants}

            with sp.Popen(vep_cmd, env=env, stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE) as proc:
                data = header + "".join(block)

                stdout_bytes, stderr_bytes = proc.communicate(data.encode())
                if stderr_bytes:
                    print(stderr_bytes)

                if proc.returncode != 0:
                    raise ValueError(
                        'VEP command '
                        + vep_cmd
                        + 'failed with non-zero exit status:'
                        + str(proc.returncode)
                        + '\n'
                        + 'VEP error output:\n'
                        + stderr_bytes.decode()
                    )

            stdout = stdout_bytes.decode()
            for line in stdout.split('\n'):
                line = line.rstrip()

                if line == '' or line.startswith('#'):
                    continue

                vep_v = None
                if consequence:
                    vep_v = Variant.from_vcf_line(line)
                    orig_v = non_star_to_orig_variants.get(str(vep_v))

                    if orig_v is None:
                        raise ValueError(
                            'VEP output variant '
                            + str(vep_v)
                            + ' not found in original variants. VEP output is '
                            + line
                        )

                    x = CONSEQUENCE_REGEX.findall(line)
                    if x:
                        first = x[0]
                        result = (orig_v, json.dumps(first[4:].split(',')), part_id, block_id)
                    else:
                        print(
                            'WARNING: No CSQ INFO field for VEP output variant '
                            + str(vep_v)
                            + '. VEP output is '
                            + line
                        )
                        result = (orig_v, None, part_id, block_id)
                else:
                    try:
                        jv = json.loads(line)
                    except json.decoder.JSONDecodeError as e:
                        msg = 'VEP failed to produce parsable JSON!\n' 'json:' + line + '\n' 'error: ' + e.msg
                        if tolerate_parse_error:
                            print(msg)
                            continue
                        raise Exception(msg) from e

                    variant_string = jv.get('input')
                    if variant_string is None:
                        raise ValueError('VEP generated null variant string\n' 'json: ' + line + '\n' 'parsed: ' + jv)
                    v = Variant.from_vcf_line(variant_string)
                    orig_v = non_star_to_orig_variants.get(str(v))
                    if orig_v is not None:
                        result = (orig_v, line, part_id, block_id)
                    else:
                        raise ValueError(
                            'VEP output variant ' + str(vep_v) + 'not found in original variants. VEP output is ' + line
                        )

                results.append(result)

            elapsed_time = time.monotonic() - start_time
            print('processed ' + str(n_processed) + ' variants in ' + str(round(elapsed_time, 2)) + 's')

    return results


def main(
    action: str,
    consequence: bool,
    tolerate_parse_error: bool,
    block_size: int,
    input_file: Optional[str],
    output_file: str,
    part_id: str,
    vep_cmd: str,
):
    vep_cmd_list = shlex.split(vep_cmd)

    if action == 'csq_header':
        csq_header = get_csq_header(vep_cmd_list) or ''
        with open(output_file, 'w') as out:
            out.write(csq_header + '\n')
    else:
        assert action == 'vep'
        assert input_file is not None
        results = run_vep(vep_cmd_list, input_file, block_size, consequence, tolerate_parse_error, part_id, os.environ)
        with gzip.open(output_file, 'wt') as out:
            out.write('variant\tvep\tpart_id\tblock_id\n')
            for v, a, part_id, block_id in results:
                out.write('\t'.join((str(x) for x in (v, a, part_id, block_id))) + '\n')


if __name__ == '__main__':
    action = sys.argv[1]

    consequence = bool(int(os.environ['VEP_CONSEQUENCE']))
    tolerate_parse_error = bool(int(os.environ['VEP_TOLERATE_PARSE_ERROR']))
    block_size = int(os.environ['VEP_BLOCK_SIZE'])
    input_file = os.environ.get('VEP_INPUT_FILE')
    output_file = os.environ['VEP_OUTPUT_FILE']
    data_dir = os.environ['VEP_DATA_MOUNT']
    part_id = os.environ['VEP_PART_ID']
    vep_cmd = os.environ['VEP_COMMAND']

    main(action, consequence, tolerate_parse_error, block_size, input_file, output_file, part_id, vep_cmd)
