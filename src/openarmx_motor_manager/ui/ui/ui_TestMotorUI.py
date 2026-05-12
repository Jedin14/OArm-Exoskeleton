# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'TestMotorUI.ui'
##
## Created by: Qt User Interface Compiler version 6.9.3
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QVBoxLayout, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(516, 588)
        self.verticalLayout_3 = QVBoxLayout(Form)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.gridLayout = QGridLayout()
        self.gridLayout.setObjectName(u"gridLayout")
        self.label_7 = QLabel(Form)
        self.label_7.setObjectName(u"label_7")

        self.gridLayout.addWidget(self.label_7, 1, 0, 1, 1)

        self.comboBox = QComboBox(Form)
        self.comboBox.setObjectName(u"comboBox")

        self.gridLayout.addWidget(self.comboBox, 0, 1, 1, 1)

        self.label_6 = QLabel(Form)
        self.label_6.setObjectName(u"label_6")

        self.gridLayout.addWidget(self.label_6, 0, 0, 1, 1)

        self.comboBox_2 = QComboBox(Form)
        self.comboBox_2.setObjectName(u"comboBox_2")

        self.gridLayout.addWidget(self.comboBox_2, 1, 1, 1, 1)


        self.verticalLayout_3.addLayout(self.gridLayout)

        self.horizontalLayout_5 = QHBoxLayout()
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.label_5 = QLabel(Form)
        self.label_5.setObjectName(u"label_5")

        self.verticalLayout.addWidget(self.label_5)

        self.label = QLabel(Form)
        self.label.setObjectName(u"label")

        self.verticalLayout.addWidget(self.label)

        self.label_2 = QLabel(Form)
        self.label_2.setObjectName(u"label_2")

        self.verticalLayout.addWidget(self.label_2)

        self.label_3 = QLabel(Form)
        self.label_3.setObjectName(u"label_3")

        self.verticalLayout.addWidget(self.label_3)

        self.label_4 = QLabel(Form)
        self.label_4.setObjectName(u"label_4")

        self.verticalLayout.addWidget(self.label_4)


        self.horizontalLayout_5.addLayout(self.verticalLayout)

        self.verticalLayout_2 = QVBoxLayout()
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.horizontalLayout_10 = QHBoxLayout()
        self.horizontalLayout_10.setObjectName(u"horizontalLayout_10")
        self.horizontalSlider_5 = QSlider(Form)
        self.horizontalSlider_5.setObjectName(u"horizontalSlider_5")
        self.horizontalSlider_5.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_10.addWidget(self.horizontalSlider_5)

        self.doubleSpinBox_5 = QDoubleSpinBox(Form)
        self.doubleSpinBox_5.setObjectName(u"doubleSpinBox_5")

        self.horizontalLayout_10.addWidget(self.doubleSpinBox_5)

        self.label_9 = QLabel(Form)
        self.label_9.setObjectName(u"label_9")
        self.label_9.setMinimumSize(QSize(40, 0))
        self.label_9.setBaseSize(QSize(0, 0))

        self.horizontalLayout_10.addWidget(self.label_9)


        self.verticalLayout_2.addLayout(self.horizontalLayout_10)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalSlider = QSlider(Form)
        self.horizontalSlider.setObjectName(u"horizontalSlider")
        self.horizontalSlider.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout.addWidget(self.horizontalSlider)

        self.doubleSpinBox = QDoubleSpinBox(Form)
        self.doubleSpinBox.setObjectName(u"doubleSpinBox")

        self.horizontalLayout.addWidget(self.doubleSpinBox)

        self.label_10 = QLabel(Form)
        self.label_10.setObjectName(u"label_10")
        self.label_10.setMinimumSize(QSize(40, 0))

        self.horizontalLayout.addWidget(self.label_10)


        self.verticalLayout_2.addLayout(self.horizontalLayout)

        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.horizontalSlider_2 = QSlider(Form)
        self.horizontalSlider_2.setObjectName(u"horizontalSlider_2")
        self.horizontalSlider_2.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_2.addWidget(self.horizontalSlider_2)

        self.doubleSpinBox_2 = QDoubleSpinBox(Form)
        self.doubleSpinBox_2.setObjectName(u"doubleSpinBox_2")

        self.horizontalLayout_2.addWidget(self.doubleSpinBox_2)

        self.label_11 = QLabel(Form)
        self.label_11.setObjectName(u"label_11")
        self.label_11.setMinimumSize(QSize(40, 0))

        self.horizontalLayout_2.addWidget(self.label_11)


        self.verticalLayout_2.addLayout(self.horizontalLayout_2)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalSlider_3 = QSlider(Form)
        self.horizontalSlider_3.setObjectName(u"horizontalSlider_3")
        self.horizontalSlider_3.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_3.addWidget(self.horizontalSlider_3)

        self.doubleSpinBox_3 = QDoubleSpinBox(Form)
        self.doubleSpinBox_3.setObjectName(u"doubleSpinBox_3")

        self.horizontalLayout_3.addWidget(self.doubleSpinBox_3)

        self.label_12 = QLabel(Form)
        self.label_12.setObjectName(u"label_12")
        self.label_12.setMinimumSize(QSize(40, 0))

        self.horizontalLayout_3.addWidget(self.label_12)


        self.verticalLayout_2.addLayout(self.horizontalLayout_3)

        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.horizontalSlider_4 = QSlider(Form)
        self.horizontalSlider_4.setObjectName(u"horizontalSlider_4")
        self.horizontalSlider_4.setOrientation(Qt.Orientation.Horizontal)

        self.horizontalLayout_4.addWidget(self.horizontalSlider_4)

        self.doubleSpinBox_4 = QDoubleSpinBox(Form)
        self.doubleSpinBox_4.setObjectName(u"doubleSpinBox_4")

        self.horizontalLayout_4.addWidget(self.doubleSpinBox_4)

        self.label_13 = QLabel(Form)
        self.label_13.setObjectName(u"label_13")
        self.label_13.setMinimumSize(QSize(40, 0))

        self.horizontalLayout_4.addWidget(self.label_13)


        self.verticalLayout_2.addLayout(self.horizontalLayout_4)


        self.horizontalLayout_5.addLayout(self.verticalLayout_2)


        self.verticalLayout_3.addLayout(self.horizontalLayout_5)

        self.horizontalLayout_6 = QHBoxLayout()
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.pushButton_2 = QPushButton(Form)
        self.pushButton_2.setObjectName(u"pushButton_2")

        self.horizontalLayout_6.addWidget(self.pushButton_2)

        self.pushButton = QPushButton(Form)
        self.pushButton.setObjectName(u"pushButton")

        self.horizontalLayout_6.addWidget(self.pushButton)


        self.verticalLayout_3.addLayout(self.horizontalLayout_6)

        self.horizontalLayout_7 = QHBoxLayout()
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.label_8 = QLabel(Form)
        self.label_8.setObjectName(u"label_8")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self.label_8.setFont(font)
        self.label_8.setWordWrap(True)

        self.horizontalLayout_7.addWidget(self.label_8)


        self.verticalLayout_3.addLayout(self.horizontalLayout_7)

        self.verticalLayout_3.setStretch(0, 1)
        self.verticalLayout_3.setStretch(1, 4)
        self.verticalLayout_3.setStretch(2, 1)
        self.verticalLayout_3.setStretch(3, 1)

        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"\u5355\u7535\u673a\u6d4b\u8bd5", None))
        self.label_7.setText(QCoreApplication.translate("Form", u"\u7535\u673aID", None))
        self.label_6.setText(QCoreApplication.translate("Form", u"CAN\u901a\u9053", None))
        self.label_5.setText(QCoreApplication.translate("Form", u"\u4f4d\u7f6e", None))
        self.label.setText(QCoreApplication.translate("Form", u"\u901f\u5ea6", None))
        self.label_2.setText(QCoreApplication.translate("Form", u"\u626d\u77e9", None))
        self.label_3.setText(QCoreApplication.translate("Form", u"KP", None))
        self.label_4.setText(QCoreApplication.translate("Form", u"KD", None))
        self.label_9.setText(QCoreApplication.translate("Form", u"Rad", None))
        self.label_10.setText(QCoreApplication.translate("Form", u"Rad/s", None))
        self.label_11.setText(QCoreApplication.translate("Form", u"N/m", None))
        self.label_12.setText("")
        self.label_13.setText("")
        self.pushButton_2.setText(QCoreApplication.translate("Form", u"\u542f\u52a8", None))
        self.pushButton.setText(QCoreApplication.translate("Form", u"\u505c\u6b62", None))
        self.label_8.setText(QCoreApplication.translate("Form", u"\u5b89\u5168\u63d0\u793a\uff1a\u5728\u719f\u6089\u7535\u673a\u6d4b\u8bd5\u5de5\u5177\u4e4b\u524d\u5c3d\u91cf\u4f7f\u7528\u8f83\u5c0f\u7684\u901f\u5ea6\u3001\u626d\u77e9\u3001KP\u3001KD\u503c\uff0c\u5c3d\u91cf\u4e0d\u8981\u8d85\u8fc7\u6700\u5927\u503c\u768410%\uff0c\u5426\u5219\u53ef\u80fd\u5f15\u53d1\u5b89\u5168\u4e8b\u6545\uff01\u671b\u6240\u4f7f\u7528\u8005\u8c28\u614e\u4f7f\u7528\uff01", None))
    # retranslateUi

