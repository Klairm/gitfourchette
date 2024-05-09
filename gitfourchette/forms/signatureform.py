from gitfourchette.porcelain import *
from gitfourchette.qt import *
from gitfourchette.toolbox import *
from gitfourchette.trtables import TrTables
from gitfourchette.forms.ui_signatureform import Ui_SignatureForm


def formatTimeOffset(minutes: int):
    p = "-" if minutes < 0 else "+"
    h = abs(minutes) // 60
    m = abs(minutes) % 60
    return f"{p}{h:02}:{m:02}"


class SignatureForm(QWidget):
    signatureChanged = Signal()

    class ReplaceWhat(enum.IntEnum):
        Author = 1
        Committer = 2
        Both = 3

    @staticmethod
    def validateInput(item: str) -> str:
        try:
            validate_signature_item(item)
            return ""
        except NameValidationError as exc:
            return TrTables.refNameValidation(exc.code)

    def __init__(self, parent):
        super().__init__(parent)
        self.ui = Ui_SignatureForm()
        self.ui.setupUi(self)
        self.primeTimeOffsetComboBox()

        self.ui.replaceComboBox.currentIndexChanged.connect(self.signatureChanged)
        self.ui.nameEdit.textChanged.connect(self.signatureChanged)
        self.ui.emailEdit.textChanged.connect(self.signatureChanged)
        self.ui.timeEdit.timeChanged.connect(self.signatureChanged)
        self.ui.offsetEdit.currentIndexChanged.connect(self.signatureChanged)
        self.ui.nowButton.clicked.connect(self.setDateTimeNow)

    def setSignature(self, signature: Signature):
        qdt: QDateTime = QDateTime.fromSecsSinceEpoch(signature.time, Qt.TimeSpec.OffsetFromUTC, signature.offset * 60)
        self.ui.nameEdit.setText(signature.name)
        self.ui.emailEdit.setText(signature.email)
        self.ui.timeEdit.setDateTime(qdt)
        self.setTimeOffset(signature.offset)

    def primeTimeOffsetComboBox(self):
        decimalCodedOffsets = [
            -11_00, -10_00, -9_00, -9_30, -8_00, -7_00,
            -6_00, -5_00, -4_00, -3_30, -3_00, -2_30, -2_00, -1_00,
            +0, +1_00, +2_00, +3_00, +3_30, +4_00, +4_30, +5_00, +5_30, +5_45,
            +6_00, +6_30, +7_00, +8_00, +9_00, +9_30, +10_00, +10_30, +11_00,
            +12_00, +13_00,
        ]

        for t in decimalCodedOffsets:
            minutes = (abs(t) // 100) * 60 + (abs(t) % 100)
            if t < 0:
                minutes = -minutes
            self.ui.offsetEdit.addItem(formatTimeOffset(minutes), minutes)

        # Prime QComboBox for local timezone (if it's missing from the list above)
        self.setDateTimeNow()

    def setTimeOffset(self, minutes):
        comboBox = self.ui.offsetEdit
        i = comboBox.findData(minutes)

        # If an obscure offset is missing from our QComboBox, insert it
        if i < 0:
            # Find where to insert it
            for i in range(comboBox.count()):
                existingOffset = comboBox.itemData(i)
                if existingOffset >= minutes:
                    break
            else:
                i = comboBox.count()
            comboBox.insertItem(i, formatTimeOffset(minutes), minutes)

        assert i >= 0
        comboBox.setCurrentIndex(i)

    def setDateTimeNow(self):
        now = QDateTime.currentDateTime()
        self.ui.timeEdit.setDateTime(now)
        self.setTimeOffset(now.offsetFromUtc() // 60)

    def installValidator(self, validator: ValidatorMultiplexer):
        validator.connectInput(self.ui.nameEdit, SignatureForm.validateInput)
        validator.connectInput(self.ui.emailEdit, SignatureForm.validateInput)

    def getSignature(self) -> Signature:
        qdt = self.ui.timeEdit.dateTime()
        return Signature(
            name=self.ui.nameEdit.text(),
            email=self.ui.emailEdit.text(),
            time=qdt.toSecsSinceEpoch(),
            offset=self.ui.offsetEdit.currentData(),
        )

    def replaceWhat(self):
        index = self.ui.replaceComboBox.currentIndex()
        return SignatureForm.ReplaceWhat(index + 1)

    def replaceAuthor(self):
        RW = SignatureForm.ReplaceWhat
        return self.replaceWhat() in [RW.Author, RW.Both]

    def replaceCommitter(self):
        RW = SignatureForm.ReplaceWhat
        return self.replaceWhat() in [RW.Committer, RW.Both]
