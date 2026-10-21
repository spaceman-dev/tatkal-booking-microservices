-- A heading with class "newpage" starts on a new page: CSS does it for HTML/PDF,
-- a real page break is inserted for Word. Diagrams use PNG in Word (SVG in HTML).

function Header(el)
  if el.classes:includes("newpage") and FORMAT:match("docx") then
    return {pandoc.RawBlock("openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'), el}
  end
end

function Image(img)
  if FORMAT:match("docx") then
    img.src = img.src:gsub("%.svg$", ".png")
  end
  return img
end
