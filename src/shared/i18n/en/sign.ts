import type { sign as source } from '../es/sign'

export const sign: typeof source = {
  tools: {
    select: 'Select',
    signature: 'Signature',
    signatureTip: 'Place your handwritten signature',
    signatureItem: 'Signature {n}',
    initialsItem: 'Initials {n}',
    newSignature: 'New signature…',
    manage: 'Manage saved signatures…',
    text: 'Text',
    textTip: 'Write on the document',
    check: 'Check',
    cross: 'Cross',
    date: 'Today’s date',
    cert: 'With certificate',
    certTip: 'Sign with your digital certificate',
    signatures: 'Signatures',
    signaturesTip: 'Verify the document’s digital signatures'
  },
  options: {
    format: 'Format',
    dateFormat: 'Date format',
    size: 'Size',
    color: 'Color',
    clickToPlace: 'Click on the document where you want to place it',
    placeSignature: 'Click or drag where you want to place the signature',
    createFirst: 'First create your signature in the Signature menu',
    drawCertBox: 'Draw the box where the signature will appear',
    signInvisible: 'Sign without a visible mark'
  },
  pad: {
    title: 'New signature',
    saveForReuse: 'Save it for later',
    use: 'Use this signature',
    draw: 'Draw',
    type: 'Type',
    image: 'Image',
    signature: 'Signature',
    initials: 'Initials',
    drawHint: 'Sign here with your mouse or finger',
    yourName: 'Your name',
    importedAlt: 'Imported signature',
    imageHint: 'Choose a photo or scan of your signature on white paper',
    noStrokes: 'No signature strokes were found in the image',
    pickImage: 'Choose image…',
    whiteRemoved: 'The white background is removed automatically.',
    ink: 'Ink',
    inkBlue: 'Blue',
    inkBlack: 'Black'
  },
  saved: {
    title: 'Saved signatures',
    done: 'Done',
    empty: 'You don’t have any saved signatures.',
    alt: 'Saved signature',
    remove: 'Delete this signature',
    privacy: 'Signatures are stored encrypted with your Windows account and never leave this computer.'
  },
  cert: {
    title: 'Sign with a digital certificate',
    usePfx: 'Use a .pfx file…',
    sign: 'Sign',
    signing: 'Signing…',
    certificate: 'Certificate',
    pfxFile: 'Certificate file',
    change: 'Change',
    issuedBy: 'Issued by {issuer}',
    validUntil: 'valid until {date}',
    notValid: 'expired or not yet valid',
    hardware: 'on a smart card or token',
    password: 'Certificate password',
    passwordHint: 'Used only for this signature; it isn’t saved.',
    wrongPassword: 'The certificate password is incorrect.',
    searching: 'Looking for certificates on this computer…',
    none: 'There are no certificates with a private key installed in your Windows account. You can use a .pfx or .p12 file.',
    issue: {
      expired: 'expired',
      notYetValid: 'not valid yet',
      weakKey: 'key too weak',
      unsupportedKey: 'unsupported key type',
      noSigningUsage: 'key usage doesn’t include signing',
      other: 'can’t be used for signing'
    },
    reason: 'Reason (optional)',
    reasonPlaceholder: 'Approved',
    location: 'Location (optional)',
    appearance: {
      signedBy: 'Digitally signed by',
      date: 'Date: {date}',
      reason: 'Reason: {text}',
      location: 'Location: {text}'
    },
    includeHandwritten: 'Include my handwritten signature',
    includeHandwrittenNone: 'Include my handwritten signature (create one first in the Signature menu)',
    invisible: 'The signature won’t leave a visible mark on the pages, but it will be embedded in the document and can be verified.',
    level: 'Signature type',
    levels: {
      basic: {
        title: 'Basic',
        text: 'Like AutoFirma or Adobe with default settings: the signing time comes from your computer’s clock. No internet needed.'
      },
      timestamp: {
        title: 'With timestamp · recommended',
        text: '{service} certifies the exact date and time of signing, instead of your computer’s clock. Needs internet and only receives a hash, never the document.',
        anyService: 'A trusted timestamp service'
      },
      longTerm: {
        title: 'Long-term (LTV)',
        text: 'Also embeds in the PDF proof that your certificate was valid at signing time (long-term validation), so the signature can still be verified many years from now, even after the certificate expires. Needs internet and takes a few more seconds.'
      }
    },
    noTimestampService: 'To sign with a timestamp, choose a service in Settings → Timestamp service.',
    lock: 'Lock the document: it can’t be changed or signed afterward',
    unlockedHint: 'If it isn’t locked, more signatures can be added (by someone else, for example), and any later change will show up when the signature is verified.',
    withoutTimestamp: 'Sign without timestamp',
    timestampOnly: 'Sign with timestamp only',
    done: 'Document signed. Save it to keep the signature.',
    notLocked: 'Signed, but not locked: the document already had signatures and this field can’t be locked. To lock it, sign in a new area.',
    fileName: '{name}-signed.pdf'
  },
  check: {
    title: 'Digital signatures in this document',
    revocation: 'Check revocation online',
    revocationTip: 'Ask the certificate authority whether the certificates have been revoked',
    checking: 'Verifying signatures…',
    none: 'This document has no digital signatures.',
    timestampAuthority: 'Timestamp authority',
    unknownSigner: 'Unknown signer',
    issuedBy: 'issued by {issuer}',
    status: {
      valid: 'Valid signature',
      timestampValid: 'Valid timestamp',
      timestampWarning: 'Timestamp with warnings',
      warning: 'Valid signature with warnings',
      unverified: 'Signature intact, identity not verified',
      invalid: 'Invalid signature',
      timestampInvalid: 'Invalid timestamp',
      unknown: 'Couldn’t be verified'
    }
  }
}
