"""
Build script for Atlas Cython extensions.

Used by the Docker builder stage:

    pip install Cython
    python setup.py build_ext --inplace

The resulting .so is copied into the runtime image. If the extension is
absent (e.g. local dev without a C toolchain) cutsheet_normalizer.py falls
back to the pure-Python row loop, so the app stays runnable.
"""

from setuptools import setup
from Cython.Build import cythonize


setup(
    name="atlas_cython_ext",
    ext_modules=cythonize(
        ["cutsheet_normalizer_fast.pyx"],
        language_level=3,
        compiler_directives={
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
    ),
    zip_safe=False,
)
