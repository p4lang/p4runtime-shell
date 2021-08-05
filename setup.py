
import os
from setuptools import setup, find_packages
project_root = os.path.dirname(os.path.realpath(__file__))

setup(
    name = "p4runtime-shell",
    version = "0.0.2",
    packages = find_packages("."),
    install_requires = [
        "ipaddr==2.2.0",
        "jedi==0.17.2",
        "ipython==7.19.0",
        "protobuf==3.14.0",
        "grpcio==1.35.0",
        "p4runtime==1.3.0",
    ],
    author = "P4 API Working Group",
    author_email = "p4-api@lists.p4.org",
    classifiers = [
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
    ],
    description = "The P4Runtime shell",
    long_description = open(project_root + "/README.md").read(),
    long_description_content_type = "text/markdown",
    license = "Apache-2.0",
    url = "https://github.com/p4lang/p4runtime-shell"
)
