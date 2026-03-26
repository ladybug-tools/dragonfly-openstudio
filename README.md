# dragonfly-openstudio
[![Build Status](https://github.com/ladybug-tools/dragonfly-openstudio/workflows/CI/badge.svg)](https://github.com/ladybug-tools/dragonfly-openstudio/actions)
[![Python 3.10](https://img.shields.io/badge/python-3.10-orange.svg)](https://www.python.org/downloads/release/python-3100/)
[![IronPython](https://img.shields.io/badge/ironpython-2.7-red.svg)](https://github.com/IronLanguages/ironpython2/releases/tag/ipy-2.7.8/)

![Dragonfly](https://www.ladybug.tools/assets/img/dragonfly.png) ![OpenStudio](https://nrel.github.io/OpenStudio-user-documentation/img/os_thumb.png)

Dragonfly extension for translation to OpenStudio.

Specifically, this package leverages [honeybee-openstudio](https://github.com/ladybug-tools/honeybee-openstudio) te extend [dragonfly-energy](https://github.com/ladybug-tools/dragonfly-energy) to perform translations to OpenStudio using the [OpenStudio](https://github.com/NREL/OpenStudio) SDK. Translation capabilities include translating dragonfly-energy District Energy Systems (DES) to OpenStudio models of the plant.

## Installation

`pip install -U dragonfly-openstudio`

## QuickStart

```console
import dragonfly_openstudio
```

## [API Documentation](http://ladybug-tools.github.io/dragonfly-openstudio/docs)

## Local Development

1. Clone this repo locally
```console
git clone git@github.com:ladybug-tools/dragonfly-openstudio

# or

git clone https://github.com/ladybug-tools/dragonfly-openstudio
```
2. Install dependencies:
```
cd dragonfly-openstudio
pip install -r dev-requirements.txt
pip install -r requirements.txt
```

3. Run Tests:
```console
python -m pytest tests/
```

4. Generate Documentation:
```console
sphinx-apidoc -f -e -d 4 -o ./docs ./dragonfly_openstudio
sphinx-build -b html ./docs ./docs/_build/docs
```