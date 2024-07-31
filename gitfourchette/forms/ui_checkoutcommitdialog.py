# Form implementation generated from reading ui file 'checkoutcommitdialog.ui'
#
# Created by: PyQt6 UI code generator 6.7.1
#
# WARNING: Any manual changes made to this file will be lost when pyuic6 is
# run again.  Do not edit this file unless you know what you are doing.


from gitfourchette.qt import *


class Ui_CheckoutCommitDialog(object):
    def setupUi(self, CheckoutCommitDialog):
        CheckoutCommitDialog.setObjectName("CheckoutCommitDialog")
        CheckoutCommitDialog.setWindowModality(Qt.WindowModality.NonModal)
        CheckoutCommitDialog.setEnabled(True)
        CheckoutCommitDialog.resize(581, 161)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(CheckoutCommitDialog.sizePolicy().hasHeightForWidth())
        CheckoutCommitDialog.setSizePolicy(sizePolicy)
        CheckoutCommitDialog.setSizeGripEnabled(False)
        CheckoutCommitDialog.setModal(True)
        self.verticalLayout = QVBoxLayout(CheckoutCommitDialog)
        self.verticalLayout.setObjectName("verticalLayout")
        self.groupBox_2 = QGroupBox(parent=CheckoutCommitDialog)
        self.groupBox_2.setObjectName("groupBox_2")
        self.verticalLayout_3 = QVBoxLayout(self.groupBox_2)
        self.verticalLayout_3.setObjectName("verticalLayout_3")
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        self.horizontalLayout.setObjectName("horizontalLayout")
        self.switchToLocalBranchRadioButton = QRadioButton(parent=self.groupBox_2)
        self.switchToLocalBranchRadioButton.setObjectName("switchToLocalBranchRadioButton")
        self.horizontalLayout.addWidget(self.switchToLocalBranchRadioButton)
        self.switchToLocalBranchComboBox = QComboBox(parent=self.groupBox_2)
        self.switchToLocalBranchComboBox.setObjectName("switchToLocalBranchComboBox")
        self.horizontalLayout.addWidget(self.switchToLocalBranchComboBox)
        self.verticalLayout_3.addLayout(self.horizontalLayout)
        self.detachedHeadRadioButton = QRadioButton(parent=self.groupBox_2)
        self.detachedHeadRadioButton.setObjectName("detachedHeadRadioButton")
        self.verticalLayout_3.addWidget(self.detachedHeadRadioButton)
        self.createBranchRadioButton = QRadioButton(parent=self.groupBox_2)
        self.createBranchRadioButton.setObjectName("createBranchRadioButton")
        self.verticalLayout_3.addWidget(self.createBranchRadioButton)
        self.verticalLayout.addWidget(self.groupBox_2)
        self.recurseSubmodulesSpacer = QFrame(parent=CheckoutCommitDialog)
        self.recurseSubmodulesSpacer.setFrameShape(QFrame.Shape.NoFrame)
        self.recurseSubmodulesSpacer.setLineWidth(0)
        self.recurseSubmodulesSpacer.setObjectName("recurseSubmodulesSpacer")
        self.verticalLayout_4 = QVBoxLayout(self.recurseSubmodulesSpacer)
        self.verticalLayout_4.setContentsMargins(0, 0, 0, 0)
        self.verticalLayout_4.setObjectName("verticalLayout_4")
        spacerItem = QSpacerItem(20, 16, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.MinimumExpanding)
        self.verticalLayout_4.addItem(spacerItem)
        self.verticalLayout.addWidget(self.recurseSubmodulesSpacer)
        self.recurseSubmodulesGroupBox = QGroupBox(parent=CheckoutCommitDialog)
        self.recurseSubmodulesGroupBox.setObjectName("recurseSubmodulesGroupBox")
        self.verticalLayout_2 = QVBoxLayout(self.recurseSubmodulesGroupBox)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.recurseSubmodulesCheckBox = QCheckBox(parent=self.recurseSubmodulesGroupBox)
        self.recurseSubmodulesCheckBox.setChecked(True)
        self.recurseSubmodulesCheckBox.setObjectName("recurseSubmodulesCheckBox")
        self.verticalLayout_2.addWidget(self.recurseSubmodulesCheckBox)
        self.verticalLayout.addWidget(self.recurseSubmodulesGroupBox)
        self.buttonBox = QDialogButtonBox(parent=CheckoutCommitDialog)
        self.buttonBox.setOrientation(Qt.Orientation.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok)
        self.buttonBox.setCenterButtons(False)
        self.buttonBox.setObjectName("buttonBox")
        self.verticalLayout.addWidget(self.buttonBox)

        self.retranslateUi(CheckoutCommitDialog)
        self.buttonBox.rejected.connect(CheckoutCommitDialog.reject) # type: ignore
        self.buttonBox.accepted.connect(CheckoutCommitDialog.accept) # type: ignore
        self.switchToLocalBranchRadioButton.toggled['bool'].connect(self.switchToLocalBranchComboBox.setEnabled) # type: ignore
        QMetaObject.connectSlotsByName(CheckoutCommitDialog)

    def retranslateUi(self, CheckoutCommitDialog):
        _translate = QCoreApplication.translate
        CheckoutCommitDialog.setWindowTitle(_translate("CheckoutCommitDialog", "Check out commit"))
        self.groupBox_2.setTitle(_translate("CheckoutCommitDialog", "How do you want to check out this commit?"))
        self.switchToLocalBranchRadioButton.setText(_translate("CheckoutCommitDialog", "Switch to &branch:"))
        self.switchToLocalBranchComboBox.setToolTip(_translate("CheckoutCommitDialog", "List of branches that point to this commit."))
        self.detachedHeadRadioButton.setText(_translate("CheckoutCommitDialog", "Enter &detached HEAD here"))
        self.createBranchRadioButton.setText(_translate("CheckoutCommitDialog", "Start &new branch here..."))
        self.recurseSubmodulesGroupBox.setTitle(_translate("CheckoutCommitDialog", "After the checkout:"))
        self.recurseSubmodulesCheckBox.setText(_translate("CheckoutCommitDialog", "Recurse into submodules"))
