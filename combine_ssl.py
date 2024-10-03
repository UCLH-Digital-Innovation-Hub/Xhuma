import os

def combine_certificates(cert_files, output_file):
    with open(output_file, 'w') as outfile:
        for fname in cert_files:
            with open(fname) as infile:
                outfile.write(infile.read())
    print(f"Combined certificates saved to {output_file}")

if __name__ == "__main__":
    # create a list of certificate files from /keys/mtls/clients
    cert_files = []
    for file in os.listdir("keys/mtls/clients"):
        if file.endswith(".crt") or file.endswith(".cer"):
            cert_files.append(f"keys/mtls/clients/{file}") 

    output_file = "keys/mtls/combined-cert.pem"
    
    # Combine the certificates
    combine_certificates(cert_files, output_file)
