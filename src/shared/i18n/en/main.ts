import type { main as source } from '../es/main'

export const main: typeof source = {
  engine: {
    stopped: 'The document engine has stopped.',
    failed: 'Document engine error.',
    tooLarge: 'The request is too large.',
    cancelled: 'Operation canceled.'
  },
  errors: {
    unexpected: 'An unexpected error occurred.',
    forbiddenOrigin: 'Origin not allowed.',
    invalidParams: 'Invalid parameters.',
    noWindow: 'The window isn’t available.',
    docClosed: 'The document is no longer open.',
    forbiddenOperation: 'This operation isn’t allowed from the interface.',
    recentGone: 'That file is no longer in the recent list.',
    openExpired: 'The request to open the file has expired.',
    imageTooLarge: 'The image is too large.',
    mergeTwo: 'Choose at least two files to combine.',
    tooManyPhotos: 'Choose up to 100 photos at a time; you can add more later.',
    compressedSameName: 'Save the compressed copy with a different name to keep the original.',
    pickPfxAgain: 'Choose the certificate file again.',
    printNotAllowed: 'The author of this PDF doesn’t allow it to be printed.'
  },
  dialogs: {
    savePdf: 'Save PDF',
    pickImage: 'Choose image',
    pickMerge: 'Choose the files to combine',
    pickImages: 'Choose images',
    pickPhotos: 'Choose the photos of the document',
    insertPages: 'Insert pages from another file',
    extractPages: 'Extract pages to a new PDF',
    filesFolder: 'Folder to save the files in',
    imagesFolder: 'Folder to save the images in',
    saveCompressed: 'Save compressed copy',
    exportText: 'Export to text',
    exportWord: 'Export to Word',
    pickPfx: 'Choose certificate file'
  },
  filters: {
    pdfAndImages: 'PDFs and images',
    images: 'Images',
    photos: 'Photos and images',
    text: 'Text',
    certificate: 'Certificate'
  },
  fileNames: {
    extract: '{name}-extract.pdf',
    compressedSuffix: '-compressed'
  },
  link: {
    title: 'Open link',
    mail: 'Write an email to this address?',
    web: 'Open this link to {host} in the browser?'
  },
  feedback: {
    failed: 'Couldn’t send your feedback. Check your internet connection and try again.',
    viaGithub: 'Feedback is sent through GitHub.',
    wait: 'Wait a few seconds before sending more feedback.'
  },
  updates: {
    mismatch: 'The download doesn’t match the published version and was discarded. Nothing was installed.',
    downloadFailed: 'Couldn’t download the new version. Try again later.',
    checkFailed: 'Couldn’t check for a new version. Check your internet connection.'
  },
  print: {
    cancelled: 'Printing canceled.',
    printerGone: 'That printer is no longer available. Choose another one.',
    noPages: 'There are no pages to print.',
    tooManySheets: 'That would be more than {max} sheets. Lower the enlargement or print fewer pages.',
    failed: 'Couldn’t print. Make sure the printer is on and connected, and try again.'
  },
  scanner: {
    testDevice: 'Test scanner',
    unnamed: 'Scanner',
    cancelled: 'Canceled.',
    failed: 'The scanner couldn’t scan. Make sure it’s on and try again.',
    notFound: 'That scanner is no longer available. Make sure it’s on and connected.',
    paperJam: 'The paper is jammed in the scanner.',
    noPaper: 'There’s no paper in the feeder.',
    paperProblem: 'There’s a problem with the paper in the scanner.',
    notResponding: 'The scanner isn’t responding. Make sure it’s on and connected.',
    busy: 'The scanner is busy. Wait a moment and try again.',
    warmingUp: 'The scanner is getting ready. Try again in a few seconds.',
    userAction: 'The scanner needs you to do something: check its screen.',
    noConnection: 'Can’t communicate with the scanner. Make sure it’s on and connected.',
    locked: 'The scanner is locked or another program is using it.',
    coverOpen: 'The scanner lid is open.',
    lampOff: 'The scanner lamp is off.'
  }
}
