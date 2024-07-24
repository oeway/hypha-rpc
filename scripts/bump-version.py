import json
import sys
import toml

def update_package_json(version):
    with open('javascript/package.json', 'r+') as f:
        data = json.load(f)
        data['version'] = version
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()

def update_pyproject_toml(version):
    with open('python/pyproject.toml', 'r+') as f:
        data = toml.load(f)
        data['project']['version'] = version
        f.seek(0)
        toml.dump(data, f)
        f.truncate()

def update_version_file(version):
    with open('python/hypha_rpc/VERSION', 'r+') as f:
        data = json.load(f)
        data['version'] = version
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()

def update_package_lock_json(version):
    with open('javascript/package-lock.json', 'r+') as f:
        data = json.load(f)
        data['version'] = version
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()

def bump_version(version):
    update_package_json(version)
    update_pyproject_toml(version)
    update_version_file(version)
    update_package_lock_json(version)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python bump-version.py <version>")
        sys.exit(1)

    version = sys.argv[1]
    bump_version(version)
    print(f"Version updated to {version} in all specified files.")
