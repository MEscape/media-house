"""The Thumbnail page: the brief on the left, the variants and the feed preview on the right."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from media_house.modules.control_center.application.thumbnail_studio import (
    AssetRow,
    StudioCatalogue,
    ThumbnailForm,
    VariantRow,
)
from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.thumbnail_viewmodel import (
    ThumbnailViewModel,
)
from media_house.modules.control_center.presentation.widgets import label, page_header

_ICON = QSize(160, 90)
_ROLE_ID = Qt.ItemDataRole.UserRole


class PreviewLabel(QLabel):
    """A picture scaled to the label, aspect kept (16:9 thumbnails)."""

    def __init__(self, minimum: QSize, accessible_name: str) -> None:
        super().__init__()
        self.setProperty("role", "preview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(minimum)
        self.setAccessibleName(accessible_name)
        self._pixmap = QPixmap()

    def show_file(self, path: str | None) -> None:
        self._pixmap = QPixmap(path) if path else QPixmap()
        self._fit()

    def has_picture(self) -> bool:
        return not self._pixmap.isNull()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._pixmap.isNull():
            self.clear()
            return
        self.setPixmap(
            self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


def _combo(accessible_name: str) -> QComboBox:
    combo = QComboBox()
    combo.setAccessibleName(accessible_name)
    return combo


def _fill(combo: QComboBox, rows: tuple[AssetRow, ...], *, none: str | None) -> None:
    keep = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if none is not None:
        combo.addItem(none, "")
    for row in rows:
        combo.addItem(row.name, row.asset_id)
    index = combo.findData(keep)
    combo.setCurrentIndex(max(index, 0))
    combo.blockSignals(False)


def _select(combo: QComboBox, asset_id: str) -> None:
    index = combo.findData(asset_id)
    if index >= 0:
        combo.setCurrentIndex(index)


class ThumbnailView(QWidget):
    def __init__(self, viewmodel: ThumbnailViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = viewmodel
        #: A reaction just uploaded: selected once the refreshed catalogue arrives.
        self._pending_reaction = ""
        self._build()
        self._wire()

    # --- layout --------------------------------------------------------------------------------

    def _build(self) -> None:
        self.video = _combo(strings.THUMB_VIDEO)
        self.line1 = QLineEdit()
        self.line1.setPlaceholderText(strings.THUMB_LINE1)
        self.line2 = QLineEdit()
        self.line2.setPlaceholderText(strings.THUMB_LINE2)
        self.accent = QCheckBox(strings.THUMB_ACCENT)
        self.accent.setChecked(True)
        self.alt1, self.alt2 = QLineEdit(), QLineEdit()
        self.alt1.setPlaceholderText(strings.THUMB_LINE1)
        self.alt2.setPlaceholderText(strings.THUMB_LINE2)

        self.reaction = _combo(strings.THUMB_REACTION)
        self.upload_reaction = QPushButton(strings.THUMB_UPLOAD_REACTION)
        self.from_footage = QPushButton(strings.THUMB_FROM_FOOTAGE)
        self.flip = QCheckBox(strings.THUMB_FLIP)
        self.candidates = QListWidget()
        self.candidates.setAccessibleName(strings.THUMB_CANDIDATES)
        self.candidates.setViewMode(QListWidget.ViewMode.IconMode)
        self.candidates.setIconSize(_ICON)
        self.candidates.setFixedHeight(_ICON.height() + 46)
        self.candidates.setFlow(QListWidget.Flow.LeftToRight)
        self.candidates.setWrapping(False)
        self.candidates.hide()

        self.products = QListWidget()
        self.products.setAccessibleName(strings.THUMB_PRODUCTS)
        self.products.setIconSize(QSize(64, 36))
        self.products.setMinimumHeight(150)
        self.import_picture = QPushButton(strings.THUMB_IMPORT_PICTURE)
        self.logo = _combo(strings.THUMB_LOGO)

        self.device = QGroupBox(strings.THUMB_DEVICE)
        self.device.setCheckable(True)
        self.device.setChecked(False)
        self.device_product = _combo(strings.THUMB_DEVICE_PRODUCT)
        self.device_name = QLineEdit()
        self.device_price = QLineEdit()
        self.device_price.setPlaceholderText("79,99 €")
        self.device_button = QLineEdit("In den Warenkorb")
        self.device_screen = _combo(strings.THUMB_DEVICE_SCREEN)
        device_form = QFormLayout(self.device)
        device_form.addRow(strings.THUMB_DEVICE_PRODUCT, self.device_product)
        device_form.addRow(strings.THUMB_DEVICE_NAME, self.device_name)
        device_form.addRow(strings.THUMB_DEVICE_PRICE, self.device_price)
        device_form.addRow(strings.THUMB_DEVICE_BUTTON, self.device_button)
        device_form.addRow(strings.THUMB_DEVICE_SCREEN, self.device_screen)

        self.badge = QLineEdit()
        self.badge.setPlaceholderText("04:30")
        self.badge.setMaxLength(6)
        self.plate = _combo(strings.THUMB_PLATE)
        self.design_button = QPushButton(strings.THUMB_DESIGN)
        self.design_button.setProperty("role", "primary")
        self.export_button = QPushButton(strings.THUMB_EXPORT)
        self.export_button.setEnabled(False)

        form = QFormLayout()
        form.addRow(strings.THUMB_VIDEO, self.video)
        headline = QVBoxLayout()
        headline.addWidget(self.line1)
        headline.addWidget(self.line2)
        headline.addWidget(self.accent)
        form.addRow(strings.THUMB_HEADLINE, headline)
        alt = QHBoxLayout()
        alt.addWidget(self.alt1)
        alt.addWidget(self.alt2)
        form.addRow(strings.THUMB_ALT, alt)
        reaction_row = QHBoxLayout()
        reaction_row.addWidget(self.reaction, 1)
        reaction_row.addWidget(self.upload_reaction)
        reaction_row.addWidget(self.from_footage)
        reaction = QVBoxLayout()
        reaction.addLayout(reaction_row)
        reaction.addWidget(self.flip)
        reaction.addWidget(self.candidates)
        form.addRow(strings.THUMB_REACTION, reaction)
        products = QVBoxLayout()
        products.addWidget(self.products)
        products.addWidget(self.import_picture)
        form.addRow(strings.THUMB_PRODUCTS, products)
        form.addRow(strings.THUMB_LOGO, self.logo)
        form.addRow(self.device)
        form.addRow(strings.THUMB_BADGE, self.badge)
        form.addRow(strings.THUMB_PLATE, self.plate)
        buttons = QHBoxLayout()
        buttons.addWidget(self.design_button)
        buttons.addWidget(self.export_button)
        form.addRow(buttons)
        brief = QWidget()
        brief.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(brief)

        self.preview = PreviewLabel(QSize(480, 270), strings.THUMB_TITLE)
        self.feed = PreviewLabel(QSize(320, 180), strings.THUMB_FEED)
        self.feed.setFixedSize(320, 180)
        self.variant_list = QListWidget()
        self.variant_list.setAccessibleName(strings.THUMB_VARIANTS)
        self.variant_list.setMaximumHeight(110)
        self.issues = label("", "note", wrap=True)
        self.empty = label(strings.THUMB_EMPTY, "empty", wrap=True)
        right = QVBoxLayout()
        right.addWidget(self.preview, 1)
        right.addWidget(self.empty)
        feed_row = QHBoxLayout()
        feed_box = QVBoxLayout()
        feed_box.addWidget(label(strings.THUMB_FEED, "muted"))
        feed_box.addWidget(self.feed)
        feed_row.addLayout(feed_box)
        variants = QVBoxLayout()
        variants.addWidget(label(strings.THUMB_VARIANTS, "heading"))
        variants.addWidget(self.variant_list)
        variants.addWidget(self.issues)
        feed_row.addLayout(variants, 1)
        right.addLayout(feed_row)
        result = QWidget()
        result.setLayout(right)

        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(result)
        splitter.setStretchFactor(1, 1)

        self.error = label("", "alert", wrap=True)
        self.error.hide()
        self.progress = QProgressBar()
        self.progress.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(page_header(strings.THUMB_TITLE, strings.THUMB_SUBTITLE))
        layout.addWidget(self.error)
        layout.addWidget(self.progress)
        layout.addWidget(splitter, 1)

    def _wire(self) -> None:
        self.upload_reaction.clicked.connect(self._upload_reaction)
        self.from_footage.clicked.connect(lambda: self._vm.suggest(self.video.currentData() or ""))
        self.import_picture.clicked.connect(self._import_picture)
        self.candidates.itemClicked.connect(
            lambda item: _select(self.reaction, item.data(_ROLE_ID))
        )
        self.design_button.clicked.connect(lambda: self._vm.design(self.form()))
        self.export_button.clicked.connect(self._export)
        self.variant_list.currentRowChanged.connect(self._show_variant)
        self.device_product.currentIndexChanged.connect(self._name_device)

        self._vm.catalogue_changed.connect(self._show_catalogue)
        self._vm.candidates_changed.connect(self._show_candidates)
        self._vm.variants_changed.connect(self._show_variants)
        self._vm.picture_added.connect(self._on_added)
        self._vm.busy_changed.connect(self._busy)
        self._vm.progress_changed.connect(self._progress)
        self._vm.error_changed.connect(self._show_error)

    # --- the brief -----------------------------------------------------------------------------

    def form(self) -> ThumbnailForm:
        products = tuple(
            self.products.item(i).data(_ROLE_ID)
            for i in range(self.products.count())
            if self.products.item(i).checkState() == Qt.CheckState.Checked
        )
        return ThumbnailForm(
            video_id=self.video.currentData() or "",
            line1=self.line1.text(),
            line2=self.line2.text(),
            accent_second=self.accent.isChecked(),
            alt_line1=self.alt1.text(),
            alt_line2=self.alt2.text(),
            reaction_id=self.reaction.currentData() or "",
            flip_reaction=self.flip.isChecked(),
            product_ids=products,
            logo_id=self.logo.currentData() or "",
            device=self.device.isChecked(),
            device_product_id=self.device_product.currentData() or "",
            device_name=self.device_name.text(),
            device_price=self.device_price.text(),
            device_button=self.device_button.text(),
            device_screen_id=self.device_screen.currentData() or "",
            badge=self.badge.text(),
            plate_id=self.plate.currentData() or "",
        )

    def pick_file(self) -> str:
        """Overridable in tests: the picture to import ("" = cancelled)."""
        path, _ = QFileDialog.getOpenFileName(
            self, strings.THUMB_PICK_IMAGE, "", strings.THUMB_IMAGE_FILTER
        )
        return path

    def save_file(self, suggested: str) -> str:
        """Overridable in tests: where to save the upload copy ("" = cancelled)."""
        path, _ = QFileDialog.getSaveFileName(
            self, strings.THUMB_SAVE_TITLE, suggested, strings.THUMB_SAVE_FILTER
        )
        return path

    def _upload_reaction(self) -> None:
        if path := self.pick_file():
            self._vm.add_reaction(path)

    def _import_picture(self) -> None:
        if path := self.pick_file():
            self._vm.import_picture(path, "product")

    def _name_device(self) -> None:
        if not self.device_name.text().strip():
            self.device_name.setText(self.device_product.currentText()[:20])

    def _export(self) -> None:
        row = self._current_variant()
        if row is None or row.asset_id is None:
            return
        if path := self.save_file(f"thumbnail_{row.name}.jpg"):
            self._vm.export(row.asset_id, path)

    # --- results -------------------------------------------------------------------------------

    def _show_catalogue(self, catalogue: StudioCatalogue) -> None:
        _fill(self.video, catalogue.videos, none=strings.THUMB_NO_VIDEO)
        reactions = catalogue.reactions + tuple(
            p
            for p in catalogue.pictures
            if p.asset_id not in {r.asset_id for r in catalogue.reactions}
        )
        _fill(self.reaction, reactions, none=None)
        if self._pending_reaction:
            _select(self.reaction, self._pending_reaction)
            self._pending_reaction = ""
        _fill(self.logo, catalogue.pictures, none=strings.THUMB_NONE)
        _fill(self.device_product, catalogue.pictures, none=strings.THUMB_NONE)
        _fill(self.device_screen, catalogue.pictures, none=strings.THUMB_NONE)
        _fill(self.plate, catalogue.pictures + catalogue.videos, none=strings.THUMB_NONE)
        checked = {
            self.products.item(i).data(_ROLE_ID)
            for i in range(self.products.count())
            if self.products.item(i).checkState() == Qt.CheckState.Checked
        }
        self.products.clear()
        for row in catalogue.pictures:
            item = QListWidgetItem(row.name)
            item.setData(_ROLE_ID, row.asset_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if row.asset_id in checked else Qt.CheckState.Unchecked
            )
            if row.path:
                item.setIcon(QIcon(row.path))
            self.products.addItem(item)

    def _show_candidates(self, rows: tuple[AssetRow, ...]) -> None:
        self.candidates.clear()
        for row in rows:
            item = QListWidgetItem(QIcon(row.path or ""), row.name)
            item.setData(_ROLE_ID, row.asset_id)
            item.setToolTip(row.note)
            self.candidates.addItem(item)
        self.candidates.setVisible(bool(rows))

    def _on_added(self, role: str, row: AssetRow) -> None:
        if role == "reaction":
            self._pending_reaction = row.asset_id

    def _show_variants(self, rows: tuple[VariantRow, ...]) -> None:
        self.variant_list.clear()
        for row in rows:
            text = row.label if row.asset_id else f"{row.label} (refused)"
            self.variant_list.addItem(text)
        if rows:
            self.variant_list.setCurrentRow(0)

    def _current_variant(self) -> VariantRow | None:
        index = self.variant_list.currentRow()
        rows = self._vm.variants
        return rows[index] if 0 <= index < len(rows) else None

    def _show_variant(self, _index: int) -> None:
        row = self._current_variant()
        path = row.path if row else None
        self.preview.show_file(path)
        self.feed.show_file(path)
        self.empty.setVisible(not self.preview.has_picture())
        self.issues.setText("\n".join(row.issues) if row else "")
        self.export_button.setEnabled(bool(row and row.asset_id))

    def _busy(self, busy: bool) -> None:
        self.design_button.setEnabled(not busy)
        self.from_footage.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)

    def _progress(self, current: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.progress.setFormat(message)

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
